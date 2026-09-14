#!/usr/bin/env python3
#!/bin/sh
""":"
exec "$(cd "$(dirname "$0")/.." && pwd)/venv/bin/python" "$0" "$@"
":"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

Rule = tuple[str, tuple[str, ...], tuple[str, ...], str]
VendorImportAllowlistEntry = tuple[str, tuple[str, ...]]
Violation = tuple[str, int, str, str, str, str]

IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "venv",
    "worktrees",
    "node_modules",
    ".next",
    ".turbo",
    ".venv",
    "dist",
    "build",
}
TS_PACKAGE_ROOTS = ("apps/admin-web/", "apps/web-relocation/", "packages/client-ts/")
TS_IMPORT_PATTERN = re.compile(r"(?:from\s+|import\s*)[\"']([^\"']+)[\"']")
TS_FORBIDDEN_PREFIXES = ("apps/api", "core", "nadili_runtime")
MIN_PYTHON = (3, 12)
BANNED_SOURCE_PATHS = (
    "core/adapters",
    "core/ai",
    "core/pipeline",
    "core/storage",
    "core/service.py",
    "core/legacy_import.py",
    "apps/api/routers/legacy_import.py",
    "apps/api/schemas/legacy_import.py",
    "apps/api/services/legacy_import.py",
    "main.py",
    "ui",
)

RULES: tuple[Rule, ...] = (
    (
        "isolated-pdf-parser",
        ("apps/pdf_parser/",),
        ("apps.api", "sqlalchemy", "psycopg", "redis", "core.credentials"),
        "PDF parser must not access backend state or credentials",
    ),
    (
        "pdf-layout-isolation",
        ("apps/api/", "core/", "nadili_runtime/"),
        ("docling", "docling_core", "docling_ibm_models", "torch", "torchvision"),
        "PDF layout models must run only in the resource-bounded parser process",
    ),
    (
        "legacy-core",
        ("core/",),
        ("apps.api", "fastapi", "packages.db", "sqlalchemy"),
        "core must stay independent from the HTTP framework and concrete DB wiring",
    ),
    (
        "backend-scaffold",
        ("apps/api/",),
        (
            "core.adapters",
            "core.ai",
            "core.pipeline",
            "core.service",
            "core.storage",
            "sqlite3",
        ),
        "backend scaffold must not pull in retired core internals",
    ),
    (
        "shared-runtime",
        ("nadili_runtime/",),
        ("apps.api", "core", "fastapi", "sqlalchemy"),
        "shared runtime helpers must stay platform-neutral",
    ),
    (
        "python-client",
        ("packages/client-python/",),
        ("apps.api", "core.storage"),
        "Python client must remain a wire-only package",
    ),
)

#: A vendor SDK and the only paths permitted to import it. The rule table above
#: forbids an import across a whole path prefix and cannot express "everything under
#: apps/api/ except one file", which is what a single-adapter boundary needs.
#: Test paths are allowed so a test can assert the adapter's effect on real SDK state;
#: they ship nothing and cannot bypass the scrubber in a running process.
VENDOR_IMPORT_ALLOWLIST: tuple[VendorImportAllowlistEntry, ...] = (
    ("sentry_sdk", ("apps/api/observability.py", "apps/api/tests/")),
)


def _walk_files(root: Path, suffixes: tuple[str, ...]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
        for filename in filenames:
            if filename.endswith(suffixes):
                yield Path(dirpath) / filename


def iter_python_files(root: Path) -> Iterable[Path]:
    yield from _walk_files(root, (".py",))


def iter_typescript_files(root: Path) -> Iterable[Path]:
    for path in _walk_files(root, (".ts", ".tsx")):
        relative = path.relative_to(root)
        if relative.as_posix().startswith(TS_PACKAGE_ROOTS):
            yield path


def rules_for_path(path: str) -> Iterable[Rule]:
    for rule in RULES:
        if any(path.startswith(prefix) for prefix in rule[1]):
            yield rule


def imported_names(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield node.module
        yield from (f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")


def imported_roots(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield node.module


def is_forbidden(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(f"{forbidden}.")


def is_path_allowed(relative: str, allowed_paths: tuple[str, ...]) -> bool:
    """Match an exact file, or any file under an entry written as a directory prefix."""
    return any(
        relative == allowed if not allowed.endswith("/") else relative.startswith(allowed)
        for allowed in allowed_paths
    )


def check_file(path: Path, root: Path) -> list[Violation]:
    relative = path.relative_to(root).as_posix()
    rules = tuple(rules_for_path(relative))
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for imported in imported_names(node):
            for area, _prefixes, forbidden_modules, reason in rules:
                for forbidden in forbidden_modules:
                    if is_forbidden(imported, forbidden):
                        violations.append(
                            (relative, node.lineno, area, imported, forbidden, reason)
                        )
        for imported in imported_roots(node):
            for vendor, allowed_paths in VENDOR_IMPORT_ALLOWLIST:
                if not is_path_allowed(relative, allowed_paths) and is_forbidden(imported, vendor):
                    violations.append(
                        (
                            relative,
                            node.lineno,
                            "vendor-import",
                            imported,
                            vendor,
                            "vendor SDK imports must stay behind the approved adapter",
                        )
                    )
    return violations


def check_boundaries(root: Path) -> list[Violation]:
    root = root.resolve()
    violations = [
        violation for path in iter_python_files(root) for violation in check_file(path, root)
    ]
    for path in iter_typescript_files(root):
        relative = path.relative_to(root).as_posix()
        package_root = next(prefix for prefix in TS_PACKAGE_ROOTS if relative.startswith(prefix))
        for line, imported in enumerate(
            TS_IMPORT_PATTERN.findall(path.read_text(encoding="utf-8")), 1
        ):
            normalized = imported.removeprefix("@/")
            if imported.startswith("@nadili/client-ts") and package_root in {
                "apps/admin-web/",
                "apps/web-relocation/",
            }:
                continue
            if imported.startswith("."):
                target = (path.parent / imported).resolve()
                if not target.is_relative_to((root / package_root).resolve()):
                    violations.append(
                        (
                            relative,
                            line,
                            "typescript-client",
                            imported,
                            "..",
                            "relative import escapes owning package",
                        )
                    )
            elif normalized.startswith(TS_FORBIDDEN_PREFIXES):
                violations.append(
                    (
                        relative,
                        line,
                        "typescript-client",
                        imported,
                        normalized.split("/")[0],
                        "web/client must not import backend or desktop runtime",
                    )
                )
    return sorted(violations, key=lambda item: (item[0], item[1], item[3]))


def check_retired_runtime_paths(root: Path) -> list[Violation]:
    """Reject source paths retired by the PostgreSQL-only HTTP cutover."""
    violations: list[Violation] = []
    for relative in BANNED_SOURCE_PATHS:
        if (root / relative).exists():
            violations.append(
                (relative, 0, "retired-runtime", relative, relative, "retired runtime")
            )
    return violations


def format_violation(violation: Violation) -> str:
    path, line, area, imported, forbidden, reason = violation
    return f"{path}:{line}: {area} must not import '{imported}' (matches '{forbidden}'): {reason}"


def main(argv: Sequence[str] | None = None) -> int:
    ensure_supported_python()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    violations = sorted(
        check_boundaries(args.root) + check_retired_runtime_paths(args.root.resolve()),
        key=lambda item: (item[0], item[1], item[3]),
    )
    if violations:
        print("Import boundary violations found:")
        for violation in violations:
            print(format_violation(violation))
        return 1
    print("Import boundaries OK")
    return 0


def ensure_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return

    required = ".".join(str(part) for part in MIN_PYTHON)
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise RuntimeError(f"Python {required}+ is required; current interpreter is {current}")


if __name__ == "__main__":
    raise SystemExit(main())
