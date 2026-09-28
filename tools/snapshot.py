#!/usr/bin/env python3
"""Publish an explicit current-source snapshot without copying local runtime state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Set
from pathlib import Path, PurePosixPath
from typing import NotRequired, TypedDict

ROOT_FILES = frozenset(
    {
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "FILE_GUIDE.md",
        "docs/DESIGN.md",
        "docs/WALKTHROUGH.md",
        "docs/assets/workflow.mmd",
        "docs/assets/workflow.svg",
        "docs/assets/layers.mmd",
        "docs/assets/layers.svg",
        "docs/assets/ownership.svg",
        "export-manifest.json",
        "snapshot.lock.json",
        "pytest.ini",
        "tools/snapshot.py",
        "tests/test_snapshot.py",
        "shared/tests/fixtures/codex_orchestration/legacy-budget-overruns-v1.json",
        "shared/tests/fixtures/codex_orchestration/legacy-unknown-agent-history-v1.json",
        "shared/tests/fixtures/codex_orchestration/legacy-review-import-v1.json",
    }
)
SOURCE_PREFIXES = (
    ".agents/skills/nadili-",
    ".claude/skills/nadili-",
    ".claude/skills/codex-",
    ".codex/agents/",
    "scripts/",
    "tests/unit/",
    "tests/fixtures/codex_orchestration/",
)
SOURCE_FILES = frozenset({".claude/settings.json", ".codex/config.toml", ".codex/orchestrator.md"})
PRIVATE_PARTS = frozenset({".git", "worktrees", ".nadili", "node_modules", "venv", "__pycache__"})
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})"),
    re.compile(rb"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{15,})"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    re.compile(rb"[a-z][a-z0-9+.-]*://[^\s/<>\"']+:[^\s/@<>\"']+@"),
)
GIT = shutil.which("git")
MAX_FILE_BYTES = 2 * 1024 * 1024


class SnapshotError(RuntimeError):
    """An export must stop before it can overwrite work or widen publication."""


def git(root: Path, *arguments: str) -> bytes:
    """Run a fixed Git executable without shell expansion or raw error disclosure."""
    if GIT is None:
        raise SnapshotError("Git is required")
    result = subprocess.run(  # noqa: S603 -- fixed executable and explicit argument vector.
        [GIT, "-C", str(root), *arguments], capture_output=True, check=False
    )
    if result.returncode:
        raise SnapshotError(f"Git operation failed: {arguments[0]}")
    return result.stdout


def safe_name(name: str) -> str:
    """Reject paths that could escape the tree or become Git/ignore expressions."""
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or str(path) != name
        or any(part in {".", ".."} for part in path.parts)
        or re.search(r"[^A-Za-z0-9_./-]", name)
        or any(part in PRIVATE_PARTS or part.startswith(".env") for part in path.parts)
        or ("state" in path.parts and path.name != ".gitignore")
        or path.suffix in {".log", ".ndjson", ".jsonl", ".stderr", ".key", ".pem"}
    ):
        raise SnapshotError("Unsafe or private path in publication inputs")
    return name


def regular_path(root: Path, name: str) -> Path:
    """Never follow destination links, including links on ancestor directories."""
    safe_name(name)
    path = root
    for part in PurePosixPath(name).parts:
        path /= part
        if path.is_symlink():
            raise SnapshotError(f"Symlink is not a publication file: {name}")
    if path.exists() and not path.is_file():
        raise SnapshotError(f"Expected a regular file: {name}")
    return path


def json_object(data: bytes) -> dict[str, object]:
    """Parse bounded local publication metadata."""
    if len(data) > MAX_FILE_BYTES:
        raise SnapshotError("Publication metadata exceeds its size limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise SnapshotError("Publication metadata must be an object")
    return value


class Replacement(TypedDict):
    """Exact reviewed substitution; occurrence count detects source drift."""

    path: str
    old: str
    new: str
    count: NotRequired[int]


def manifest(data: bytes) -> tuple[dict[str, str], list[Replacement]]:
    value = json_object(data)
    version = value.get("version")
    keys = {"version", "files", "replacements"}
    if version == 2:
        keys.add("destinations")
    if set(value) != keys or version not in {1, 2}:
        raise SnapshotError("Unsupported export manifest")
    files = value["files"]
    replacements = value["replacements"]
    if not isinstance(files, list) or not files or len(files) > 500:
        raise SnapshotError("Manifest must name between 1 and 500 exact files")
    if any(not isinstance(name, str) for name in files) or len(set(files)) != len(files):
        raise SnapshotError("Manifest file names must be unique strings")
    for name in files:
        safe_name(name)
        if name not in SOURCE_FILES and not name.startswith(SOURCE_PREFIXES):
            raise SnapshotError(f"Outside the harness source boundary: {name}")
    if not isinstance(replacements, list):
        raise SnapshotError("Manifest replacements must be a list")
    for replacement in replacements:
        if (
            not isinstance(replacement, dict)
            or set(replacement) not in ({"path", "old", "new"}, {"path", "old", "new", "count"})
            or any(not isinstance(replacement[k], str) for k in ("path", "old", "new"))
            or type(replacement.get("count", 1)) is not int
            or not 1 <= replacement.get("count", 1) <= 1000
            or replacement["path"] not in files
            or not replacement["old"]
        ):
            raise SnapshotError("Invalid explicit publication replacement")
    destinations = {name: f"snapshot/{name}" for name in files}
    if version == 2:
        proposed = value["destinations"]
        if not isinstance(proposed, dict) or set(proposed) != set(files):
            raise SnapshotError("Every source needs exactly one destination")
        for target in proposed.values():
            if not isinstance(target, str):
                raise SnapshotError("Invalid export destination")
            safe_name(target)
            if not target.startswith(("shared/", "adapters/TRIP/", "adapters/gstack/")):
                raise SnapshotError("Destination outside the publication trees")
        if len(set(proposed.values())) != len(files):
            raise SnapshotError("Export destinations must be unique")
        destinations = proposed
    targets = set(destinations.values())
    for target in targets:
        if any(str(parent) in targets for parent in PurePosixPath(target).parents):
            raise SnapshotError("Export file/directory destination collision")
    return destinations, replacements


def content_check(name: str, data: bytes) -> None:
    """Catch common credentials without printing their values."""
    if len(data) > MAX_FILE_BYTES or b"\0" in data:
        raise SnapshotError(f"Non-text or oversized publication file: {name}")
    data.decode("utf-8")
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise SnapshotError(f"Possible credential material: {name} (value withheld)")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ignore_file(names: Set[str]) -> bytes:
    lines = [
        "# Generated by tools/snapshot.py. Only the explicit public files are visible to Git.",
        "# Ignored state stays local; the pre-commit guard also rejects force-added files.",
        "*",
        "!*/",
    ]
    return ("\n".join(lines + [f"!/{name}" for name in sorted(names)]) + "\n").encode()


def write_file(root: Path, name: str, data: bytes, mode: str = "100644") -> None:
    destination = regular_path(root, name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.chmod(temporary, 0o755 if mode == "100755" else 0o644)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def lock_entries(data: bytes) -> dict[str, dict[str, str]]:
    lock = json_object(data)
    if set(lock) != {"version", "source_revision", "files"} or lock["version"] != 1:
        raise SnapshotError("Invalid snapshot lock")
    if not isinstance(lock["source_revision"], str) or not re.fullmatch(
        r"[a-f0-9]{40,64}", lock["source_revision"]
    ):
        raise SnapshotError("Invalid source revision")
    entries = lock["files"]
    if not isinstance(entries, dict):
        raise SnapshotError("Invalid snapshot file list")
    for name, entry in entries.items():
        safe_name(name)
        if (
            not name.startswith(("snapshot/", "shared/", "adapters/TRIP/", "adapters/gstack/"))
            or not isinstance(entry, dict)
            or set(entry) != {"sha256", "mode"}
            or entry["mode"] not in {"100644", "100755"}
            or not isinstance(entry["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
        ):
            raise SnapshotError("Invalid snapshot file entry")
    return entries


def path_catalog(files: dict[str, str]) -> dict[str, str]:
    """Map directory references to the common destination of exported descendants."""
    catalog = dict(files)
    candidates: dict[str, set[str]] = {}
    for source, target in files.items():
        source_path, target_path = PurePosixPath(source), PurePosixPath(target)
        for depth, parent in enumerate(source_path.parents, start=1):
            if str(parent) == "." or depth >= len(target_path.parts):
                continue
            candidates.setdefault(str(parent) + "/", set()).add(
                str(target_path.parents[depth - 1]) + "/"
            )
    for name, paths in candidates.items():
        common = posixpath.commonpath(sorted(paths))
        if common and common != ".":
            catalog[name] = common.rstrip("/") + "/"
    return catalog


def strip_document_metadata(text: str) -> str:
    """Remove visible document profiles while preserving native skill manifests and examples."""
    header = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
    if header is None:
        return text
    fields = header.group(1)
    if not re.search(r"^document_profile:", fields, re.MULTILINE):
        return text
    if re.search(r"^name:", fields, re.MULTILINE):
        return text
    return text[header.end() :].lstrip("\n")


def relink(source: str, target: str, data: bytes, files: dict[str, str]) -> bytes:
    """Rewrite selected source references; preserve external/private runtime references."""
    catalog = path_catalog(files)
    source_parent = posixpath.dirname(source)
    target_parent = posixpath.dirname(target)

    def resolve(value: str) -> str | None:
        # Prefer repository-root paths, then resolve document-relative paths.
        if value in catalog:
            return catalog[value]
        normalized = posixpath.normpath(posixpath.join(source_parent, value))
        if value.endswith("/"):
            normalized += "/"
        if normalized in catalog:
            return catalog[normalized]
        # Shims are installed one directory above their stored templates in Nadili.
        if "/shims/" in source and value.startswith("../nadili-process/"):
            return catalog.get(".claude/skills/" + value.removeprefix("../"))
        return None

    text = data.decode()
    saved: list[str] = []

    def protect(value: str) -> str:
        saved.append(value)
        return f"\x01{len(saved) - 1}\x02"

    def markdown_link(match: re.Match[str]) -> str:
        label, value = match.group(1), match.group(2)
        path, separator, fragment = value.partition("#")
        mapped = resolve(path)
        if mapped is None:
            return protect(match.group(0))
        relative = posixpath.relpath(mapped, target_parent or ".")
        return protect(f"[{label}]({relative}{separator}{fragment})")

    text = re.sub(r"\[([^]\n]*)\]\(([^()\s]+)\)", markdown_link, text)

    def code_ref(match: re.Match[str]) -> str:
        value = match.group(1)
        mapped = resolve(value)
        if mapped is not None:
            return protect(f"`{mapped}`")
        return match.group(0)

    text = re.sub(r"(?<!`)`([^`\n]+)`(?!`)", code_ref, text)
    # Whole source paths in prose, shell examples and role configuration.
    names = sorted(catalog, key=len, reverse=True)
    pattern = r"(?<![A-Za-z0-9_./-])(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_.-])"
    text = re.sub(pattern, lambda match: catalog[match.group(0)], text)
    text = re.sub(r"\x01(\d+)\x02", lambda match: saved[int(match.group(1))], text)
    text = text.replace("canonicality: canonical\n", "canonicality: derived\n")
    if PurePosixPath(target).suffix == ".md":
        text = strip_document_metadata(text)
    return text.encode()


def sync(root: Path, source: Path) -> None:
    """Copy approved blobs from one pinned commit; never copy a checkout directory."""
    files, replacements = manifest(regular_path(root, "export-manifest.json").read_bytes())
    revision = git(source, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    modes: dict[str, str] = {}
    for record in git(source, "ls-tree", "-r", "-z", revision, "--", *files).split(b"\0"):
        if record:
            metadata, raw_name = record.split(b"\t", 1)
            modes[raw_name.decode()] = metadata.decode().split()[0]
    if set(modes) != set(files) or any(mode not in {"100644", "100755"} for mode in modes.values()):
        raise SnapshotError("Manifest source missing, not a regular tracked file, or a symlink")
    blobs: dict[str, bytes] = {}
    entries: dict[str, dict[str, str]] = {}
    for name in files:
        data = git(source, "show", f"{revision}:{name}")
        for replacement in replacements:
            if replacement["path"] == name:
                old = replacement["old"].encode()
                if data.count(old) != replacement.get("count", 1):
                    raise SnapshotError(f"Publication replacement drift: {name}")
                data = data.replace(old, replacement["new"].encode())
        target = files[name]
        if not target.startswith("snapshot/") and PurePosixPath(name).suffix in {".md", ".toml"}:
            data = relink(name, target, data, files)
        content_check(target, data)
        regular_path(root, target)
        blobs[target] = data
        entries[target] = {"sha256": digest(data), "mode": modes[name]}
    lock_path = regular_path(root, "snapshot.lock.json")
    previous = lock_entries(lock_path.read_bytes()) if lock_path.exists() else {}
    for name in previous.keys() | entries.keys():
        path = regular_path(root, name)
        if path.exists():
            accepted = {
                (e["sha256"], e["mode"]) for e in (previous.get(name), entries.get(name)) if e
            }
            actual_mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            if (digest(path.read_bytes()), actual_mode) not in accepted:
                raise SnapshotError(f"Local snapshot edit would be overwritten: {name}")
    ignored = regular_path(root, ".gitignore")
    new_ignore = ignore_file(ROOT_FILES | entries.keys())
    if ignored.exists() and ignored.read_bytes() not in {
        new_ignore,
        ignore_file(ROOT_FILES | previous.keys()),
    }:
        raise SnapshotError("Local .gitignore differs from the generated publication boundary")
    for name, data in blobs.items():
        write_file(root, name, data, entries[name]["mode"])
    for name in previous.keys() - entries.keys():
        regular_path(root, name).unlink(missing_ok=True)
    write_file(root, ".gitignore", new_ignore)
    lock = {"version": 1, "source_revision": revision, "files": entries}
    write_file(
        root, "snapshot.lock.json", (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode()
    )
    print(
        f"Synced {len(entries)} files from {revision[:12]}; "
        "no staging, commits or network activity."
    )


def check(root: Path, *, staged: bool = False) -> None:
    """Validate the actual index before commits, or the local reviewable working copy."""

    def read(name: str) -> bytes:
        return git(root, "show", f":{name}") if staged else regular_path(root, name).read_bytes()

    files, _ = manifest(read("export-manifest.json"))
    entries = lock_entries(read("snapshot.lock.json"))
    expected = set(files.values())
    if set(entries) != expected:
        raise SnapshotError("Manifest and snapshot lock disagree; run sync")
    allowed = ROOT_FILES | expected
    if read(".gitignore") != ignore_file(allowed):
        raise SnapshotError(".gitignore does not match the explicit publication boundary")
    indexed: dict[str, str] = {}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if record:
            metadata, raw_name = record.split(b"\t", 1)
            mode, _, stage = metadata.decode().split()
            if stage != "0":
                raise SnapshotError("Resolve index conflicts before publication")
            indexed[raw_name.decode()] = mode
    visible = set(indexed)
    if not staged:
        visible.update(
            p.decode()
            for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
            if p
        )
    if visible != allowed:
        raise SnapshotError(
            f"Publication file set mismatch: {len(visible - allowed)} unexpected, "
            f"{len(allowed - visible)} missing; no file contents disclosed"
        )
    for name in sorted(allowed):
        data = read(name)
        mode = (
            indexed[name]
            if staged
            else ("100755" if regular_path(root, name).stat().st_mode & 0o111 else "100644")
        )
        if mode not in {"100644", "100755"}:
            raise SnapshotError(f"Non-regular publication entry: {name}")
        content_check(name, data)
        if name in entries and (
            digest(data) != entries[name]["sha256"] or mode != entries[name]["mode"]
        ):
            raise SnapshotError(f"Snapshot differs from the recorded export: {name}")
    scope = "index" if staged else "working tree"
    print(f"Publication check passed: {len(allowed)} explicit files ({scope}).")


def install_hook(root: Path) -> None:
    """Install only this repo's local guard; do not replace an existing unrelated hook."""
    hook = Path(git(root, "rev-parse", "--git-path", "hooks/pre-commit").decode().strip())
    if not hook.is_absolute():
        hook = root / hook
    expected = root / ".git/hooks/pre-commit"
    if hook != expected or any(path.is_symlink() for path in (hook, hook.parent, root / ".git")):
        raise SnapshotError("Custom hook location requires manual integration")
    data = (
        b"#!/bin/sh\n# nadili-harness publication guard\nset -eu\n"
        b'root="$(git rev-parse --show-toplevel)"\n'
        b'exec python3 "$root/tools/snapshot.py" check --staged\n'
    )
    if hook.exists() and hook.read_bytes() != data:
        raise SnapshotError("Existing pre-commit hook preserved; integrate the check manually")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(data)
    hook.chmod(0o755)
    print("Installed local publication guard; no commit was created.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "check", "install-hook"))
    parser.add_argument(
        "--source", type=Path, help="Nadili checkout; defaults to sibling ../nadili"
    )
    parser.add_argument("--staged", action="store_true", help="Check exactly the Git index")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "sync":
            sync(root, args.source or root.parent / "nadili")
        elif args.command == "check":
            check(root, staged=args.staged)
        else:
            install_hook(root)
    except (SnapshotError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__
        print(f"Publication stopped: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
