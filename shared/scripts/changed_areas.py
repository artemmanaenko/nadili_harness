#!/usr/bin/env python3
"""Classify changed files into monorepo CI areas."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

OUTPUTS = (
    "backend",
    "infra",
    "docs",
    "contracts",
    "web",
    "mobile",
    "boundaries",
    "security",
    "unclassified",
)

PYTHON_CHECK_FILES = {
    "pyproject.toml",
    "requirements-dev.in",
    "requirements-dev.txt",
    "requirements.in",
    "requirements.txt",
    "scripts/check.sh",
    "scripts/check_boundaries.py",
    "scripts/changed_areas.py",
    "scripts/ci.sh",
    "scripts/lint.sh",
    "scripts/pre-commit-fast.sh",
    "scripts/pre-commit.sh",
    "scripts/test-all.sh",
    "scripts/test.sh",
}
CONTRACT_CHECK_FILES = {
    "scripts/check_openapi_contract.py",
    "scripts/export_openapi.py",
    "scripts/client.sh",
}

JS_WORKSPACE_FILES = {"package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "turbo.json"}
SECURITY_FILES = {
    ".dockerignore",
    ".github/dependabot.yml",
    "apps/api/Dockerfile",
    "pyproject.toml",
    "requirements-dev.in",
    "requirements-dev.txt",
    "requirements.in",
    "requirements.txt",
    "scripts/lock.sh",
}

# Changes to these files can alter CI routing or bootstrap behavior.  They must
# exercise every implemented product gate rather than relying on a narrow path
# match.  New paths deliberately fall through to ``unclassified`` below; the
# required quality gate then fails closed until their ownership is explicit.
CONTROL_PLANE_FILES = {".gitignore", ".gitleaks.toml", "run.sh", "setup.sh"}
CONTROL_PLANE_PREFIXES = (".github/", "scripts/")
INSTRUCTION_ONLY_PREFIXES = (".claude/", ".codex/", ".agents/")
INSTRUCTION_ONLY_NAMES = frozenset({"AGENTS.md", "CLAUDE.md", "skills-lock.json"})
IMPLEMENTED_CONTROL_PLANE_AREAS = (
    "backend",
    "infra",
    "contracts",
    "web",
    "boundaries",
    "security",
)


def normalize(path: str) -> str:
    path = path.strip().replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def starts(path: str, *prefixes: str) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def ends(path: str, *suffixes: str) -> bool:
    return any(path.endswith(suffix) for suffix in suffixes)


def classify_one(path: str, areas: dict[str, bool]) -> None:
    if starts(path, *INSTRUCTION_ONLY_PREFIXES) or path.rsplit("/", 1)[-1] in (
        INSTRUCTION_ONLY_NAMES
    ):
        areas["docs"] = True
        return

    matched = False

    if path in CONTROL_PLANE_FILES or starts(path, *CONTROL_PLANE_PREFIXES):
        for area in IMPLEMENTED_CONTROL_PLANE_AREAS:
            areas[area] = True
        matched = True

    if path in PYTHON_CHECK_FILES:
        areas["backend"] = True
        matched = True

    # The retired desktop client's leftover source is not a PR-gated surface.
    if starts(path, "core/", "tests/"):
        matched = True

    if starts(path, "apps/", "nadili_runtime/") or path in {
        ".dockerignore",
        "pyproject.toml",
        "scripts/backend.sh",
        "scripts/lock.sh",
    }:
        areas["backend"] = True
        matched = True

    if starts(path, ".github/workflows/", "infra/") or path in {
        ".dockerignore",
        ".github/dependabot.yml",
        "apps/api/Dockerfile",
        "scripts/backend.sh",
        "scripts/lock.sh",
    }:
        areas["infra"] = True
        matched = True

    if starts(path, "docs/") or path.endswith(".md"):
        areas["docs"] = True
        matched = True

    if (
        path in CONTRACT_CHECK_FILES
        or starts(path, "apps/api/")
        or starts(path, "packages/client-ts/", "packages/client-python/", "packages/contracts/")
        or ends(path, ".openapi.json", ".openapi.yaml")
    ):
        areas["contracts"] = True
        matched = True

    # `apps/web-relocation/src/lib/brand-assets.test.ts` reads `docs/brand/nadili/*.svg`, so these
    # assets gate the web area despite living under docs/. Without this they classify as docs-only
    # and the whole gate is skipped for a change that breaks a public web test.
    if starts(path, "docs/brand/"):
        areas["web"] = True
        matched = True

    if (
        path in {"scripts/web.sh", "scripts/backend.sh", "tests/unit/test_web_script.py"}
        or path in JS_WORKSPACE_FILES
        or starts(
            path,
            "apps/admin-web/",
            "apps/web-relocation/",
            "apps/web/",
            "packages/client-ts/",
            "packages/config-ts/",
            "packages/ui-tokens/",
        )
    ):
        areas["web"] = True
        matched = True

    if path in JS_WORKSPACE_FILES or starts(
        path,
        "apps/mobile/",
        "apps/mobile-rn/",
        "packages/client-ts/",
        "packages/config-ts/",
        "packages/ui-tokens/",
    ):
        areas["mobile"] = True
        matched = True

    if path == "scripts/check_boundaries.py" or starts(
        path, "apps/", "nadili_runtime/", "packages/"
    ):
        areas["boundaries"] = True
        matched = True

    if (
        starts(path, ".github/workflows/", "infra/")
        or path in SECURITY_FILES
        or ends(
            path,
            "requirements-dev.in",
            "requirements-dev.txt",
            "requirements.in",
            "requirements.txt",
        )
    ):
        areas["security"] = True

        matched = True

    if not matched:
        areas["unclassified"] = True


def classify_paths(paths: Iterable[str]) -> dict[str, bool]:
    areas = {name: False for name in OUTPUTS}
    for path in (normalize(path) for path in paths):
        if path:
            classify_one(path, areas)
    return areas


def git_files(base: str | None, head: str | None, all_files: bool, staged: bool) -> list[str]:
    if staged:
        command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRD"]
    elif all_files:
        command = ["git", "ls-files"]
    elif base and head:
        command = ["git", "diff", "--name-only", base, head]
    else:
        command = ["git", "diff", "--name-only", "HEAD"]

    result = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    return [line for line in result.stdout.splitlines() if line.strip()]


def is_docs_only(paths: Iterable[str]) -> bool:
    """Return whether every path independently classifies as documentation only."""
    normalized_paths = [normalize(path) for path in paths]
    if not normalized_paths or any(not path for path in normalized_paths):
        return False
    return all(
        (path_areas := classify_paths([path]))["docs"]
        and not any(path_areas[name] for name in OUTPUTS if name != "docs")
        for path in normalized_paths
    )


def write_github_output(path: Path, files: Sequence[str], areas: dict[str, bool]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for name in OUTPUTS:
            output.write(f"{name}={str(areas[name]).lower()}\n")
        output.write("changed_files<<EOF\n")
        output.write("\n".join(files))
        output.write("\nEOF\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--docs-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    files = [normalize(path) for path in args.files] or git_files(
        args.base, args.head, args.all, args.staged
    )
    areas = classify_paths(files)

    if args.docs_only:
        return 0 if is_docs_only(files) else 1

    if args.github_output:
        write_github_output(args.github_output, files, areas)
    if args.json:
        print(json.dumps({"areas": areas, "files": files}, indent=2, sort_keys=True))
    else:
        for name in OUTPUTS:
            print(f"{name}={str(areas[name]).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
