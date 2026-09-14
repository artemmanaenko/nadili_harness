#!/usr/bin/env python3
"""Verify and repair the installed pre-commit hook."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

CANONICAL_HOOK = Path(__file__).resolve().parent / "hooks" / "pre-commit"


def installed_hook() -> Path | None:
    """Return the worktree-aware installed hook path, or None outside a Git checkout."""
    git = shutil.which("git")
    if git is None:
        return None
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [git, "rev-parse", "--git-path", "hooks/pre-commit"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    reported = Path(result.stdout.strip())
    # Resolve the directory but never the final component: `resolve()` on the whole path
    # dereferences a symlinked hook, and repairing the resolved target would overwrite an
    # unrelated file instead of the hook entry.
    return reported.parent.resolve() / reported.name


def is_executable(path: Path) -> bool:
    """Return whether at least one execute permission is set on path."""
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def ownership_error(path: Path, owner: int) -> str:
    """Build an actionable refusal for a hook owned by another uid."""
    quoted_path = shlex.quote(str(path))
    return (
        f"pre-commit hook {path} is owned by uid {owner}, not the current uid {os.getuid()}.\n"
        f"Run either one of these, then re-run this checker:\n"
        f"  sudo chown $(id -u) {quoted_path}   # take ownership, keeping the file\n"
        f"  sudo rm {quoted_path}               # or drop it and let the checker reinstall it"
    )


def symlink_error(path: Path) -> str:
    """Build a refusal for a hook that is a symlink rather than a regular file."""
    quoted_path = shlex.quote(str(path))
    return (
        f"pre-commit hook {path} is a symlink, not a regular file.\n"
        f"That is a deliberate setup this checker will not silently replace. Point it at "
        f"scripts/hooks/pre-commit yourself, or remove the link and re-run:\n"
        f"  rm {quoted_path}"
    )


def repair(path: Path, content: bytes) -> None:
    """Atomically replace path with executable canonical content."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o700)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report hook drift without repairing it",
    )
    args = parser.parse_args(argv)

    path = installed_hook()
    if path is None:
        return 0

    try:
        canonical_content = CANONICAL_HOOK.read_bytes()
    except OSError as exc:
        print(f"error: cannot read canonical hook {CANONICAL_HOOK}: {exc}", file=sys.stderr)
        return 1

    try:
        # lstat, not stat: a symlink's own ownership is what governs whether we may touch it.
        hook_stat = path.lstat()
    except FileNotFoundError:
        hook_stat = None
    except OSError as exc:
        print(f"error: cannot inspect installed hook {path}: {exc}", file=sys.stderr)
        return 1

    # Symlink before ownership, deliberately: ownership_error() suggests `chown`, which follows
    # the link and would change an unrelated target's owner.
    if hook_stat is not None and stat.S_ISLNK(hook_stat.st_mode):
        print(symlink_error(path), file=sys.stderr)
        return 1

    if hook_stat is not None and hook_stat.st_uid != os.getuid():
        print(ownership_error(path, hook_stat.st_uid), file=sys.stderr)
        return 1

    drifted = hook_stat is None or path.read_bytes() != canonical_content or not is_executable(path)
    if not drifted:
        return 0

    if args.check:
        print(f"pre-commit hook drift detected at {path}", file=sys.stderr)
        return 1

    try:
        repair(path, canonical_content)
    except OSError as exc:
        print(
            f"error: cannot repair pre-commit hook at {path}: {exc}\n"
            f"Ensure the hook directory is writable, then re-run the checker.",
            file=sys.stderr,
        )
        return 1

    print(f"repaired pre-commit hook at {path}; repair applies from the next commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
