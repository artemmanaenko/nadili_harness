#!/usr/bin/env python3
"""Run and conservatively reuse the fixed deterministic Python checks."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

RECEIPT_VERSION = 1
IDENTITY_VERSION = 1
MAX_RECEIPT_BYTES = 16_384
IGNORED_ENV = {
    "_",
    "SHLVL",
    "NADILI_GATE_RUN_ID",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_OPTIONAL_LOCKS",
    "GIT_EDITOR",
    "GIT_AUTHOR_DATE",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "NADILI_GATE_LEASE_FD",
    "NADILI_GATE_LEASE_TOKEN",
    "NADILI_GATE_LEASE_OWNER",
    "NADILI_GATE_LEASE_ROOT",
}
CHECKS = {"python-static", "python-unit"}
DERIVED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class Command(NamedTuple):
    label: str
    argv: tuple[str, ...]


class UnsupportedIdentity(ValueError):
    """A valid check can run, but one of its inputs cannot be fingerprinted safely."""


def _run(argv: list[str], *, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 -- callers supply closed, fixed Git argv; no shell.
        argv, cwd=cwd, check=check, capture_output=True, text=True
    )
    return result.stdout.strip()


def _git_paths(argv: list[str], *, cwd: Path) -> list[str]:
    result = subprocess.run(  # noqa: S603 -- fixed Git argv; NUL output is data, no shell.
        argv, cwd=cwd, check=True, capture_output=True
    )
    return [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def assert_clean_snapshot(root: Path, head: str | None = None, tree: str | None = None) -> str:
    """Require an unambiguous source snapshot for routing or range checks."""
    root = root.resolve()
    if head is not None:
        actual = _run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=root)
        expected = _run(["git", "rev-parse", "--verify", f"{head}^{{commit}}"], cwd=root)
        if actual != expected:
            raise ValueError("HEAD does not match the requested candidate")
    command = ["git", "diff", "--quiet"]
    if head is not None:
        command.extend(["HEAD", "--"])
    changed = subprocess.run(command, cwd=root)  # noqa: S603 -- fixed Git diff argv, no shell.
    if changed.returncode != 0:
        raise ValueError("index or worktree differs from HEAD")
    untracked = _git_paths(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
    if untracked:
        raise ValueError("nonignored untracked files are present")
    current_tree = _run(["git", "write-tree"], cwd=root)
    if tree is not None and tree != current_tree:
        raise ValueError("staged candidate changed during validation")
    return current_tree


def _file_identity(path: Path) -> bytes:
    digest = hashlib.sha256()
    if not path.exists() and not path.is_symlink():
        digest.update(b"MISSING")
        return digest.digest()
    info = path.lstat()
    digest.update(str(stat.S_IMODE(info.st_mode)).encode())
    if path.is_symlink():
        target = os.readlink(path)
        digest.update(b"L" + len(os.fsencode(target)).to_bytes(8, "big") + os.fsencode(target))
        resolved = path.resolve(strict=True)
        if resolved.is_dir():
            # Source-directory contents are expanded by _source_paths before hashing.
            return digest.digest()
        if not resolved.is_file():
            raise UnsupportedIdentity("source symlink does not resolve to a file")
        _hash_bytes(resolved, digest)
    elif path.is_file():
        _hash_bytes(path, digest)
    else:
        raise UnsupportedIdentity("source input is not a regular file")
    return digest.digest()


def _hash_bytes(path: Path, digest: hashlib._Hash) -> None:
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)


def _source_paths(root: Path) -> list[Path]:
    paths = {root / item for item in _git_paths(["git", "ls-files", "-z"], cwd=root)}
    untracked = _git_paths(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
    paths.update(root / item for item in untracked)
    ignored = _git_paths(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"], cwd=root
    )
    paths.update(root / item for item in ignored if Path(item).suffix in {".py", ".pyi"})
    linked_files: set[Path] = set()
    for path in paths:
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError) as exc:
                raise UnsupportedIdentity("external or broken source symlink") from exc
            if resolved.is_dir():
                linked_files.update(_dependency_paths([resolved]))
            elif not resolved.is_file():
                raise UnsupportedIdentity("source symlink is not a file")
    for path in linked_files:
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise UnsupportedIdentity("external or broken linked source input") from exc
    paths.update(linked_files)
    return sorted(paths, key=lambda item: os.fsencode(item.relative_to(root)))


def _tree_identity(paths: list[Path], base: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        try:
            relative = os.fsencode(path.relative_to(base))
        except ValueError:
            relative = os.fsencode(path.resolve())
        file_digest = _file_identity(path)
        digest.update(len(relative).to_bytes(8, "big") + relative + file_digest)
    return digest.hexdigest()


def _python_environment(root: Path) -> tuple[str, list[Path], list[Path], list[Path]]:
    try:
        output = _run(_fixed_command("--inputs"), cwd=root)
    except subprocess.CalledProcessError as exc:
        raise UnsupportedIdentity("activated runner inputs could not be discovered") from exc
    executable, paths, import_paths, tools = json.loads(output)
    library_paths = [Path(paths[name]).resolve() for name in ("purelib", "platlib", "stdlib")]
    resolved_imports = [
        (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        for value in import_paths
    ]
    resolved_tools = [Path(value).resolve(strict=True) for value in tools.values() if value]
    return str(executable), library_paths, resolved_imports, resolved_tools


def _dependency_paths(libraries: list[Path]) -> list[Path]:
    def raise_walk_error(error: OSError) -> None:
        raise UnsupportedIdentity("unreadable Python input tree") from error

    files: set[Path] = set()
    for library in libraries:
        if not library.exists():
            raise UnsupportedIdentity("Python dependency directory is missing")
        for directory, dirnames, filenames in os.walk(
            library, followlinks=False, onerror=raise_walk_error
        ):
            current = Path(directory)
            symlink_dirs = [name for name in dirnames if (current / name).is_symlink()]
            relevant_symlinks = [name for name in symlink_dirs if name != "site-packages"]
            if relevant_symlinks:
                raise UnsupportedIdentity("symlinked Python dependency directory")
            dirnames[:] = [name for name in dirnames if name not in symlink_dirs]
            dirnames[:] = sorted(name for name in dirnames if name not in DERIVED_DIRS)
            for filename in filenames:
                path = current / filename
                if path.suffix == ".pyc" or "__pycache__" in path.parts:
                    continue
                if path.is_symlink():
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, ValueError) as exc:
                        raise UnsupportedIdentity("broken Python dependency symlink") from exc
                    if not target.is_file():
                        raise UnsupportedIdentity("invalid Python dependency symlink")
                if path.is_file() or path.is_symlink():
                    files.add(path)
    return sorted(files, key=lambda item: os.fsencode(item))


def _environment_identity() -> str:
    values = []
    for key, value in os.environ.items():
        if key in IGNORED_ENV:
            continue
        if key == "PATH":
            # Repeated Git exec-path prefixes do not change executable resolution.
            value = os.pathsep.join(dict.fromkeys(value.split(os.pathsep)))
        values.append((key, value))
    encoded = json.dumps(sorted(values), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def input_identity(root: Path, check: str) -> str:
    if check not in CHECKS:
        raise ValueError("unknown evidence check")
    root = root.resolve()
    git = shutil.which("git")
    bash = shutil.which("bash")
    if git is None or bash is None:
        raise UnsupportedIdentity("required local tool is unavailable")
    if (
        subprocess.run(  # noqa: S603 -- fixed Git argv; no shell.
            [git, "diff", "--quiet"], cwd=root
        ).returncode
        != 0
    ):
        raise UnsupportedIdentity("index and worktree differ")
    source = _source_paths(root)
    digest = hashlib.sha256()
    digest.update(f"identity-v{IDENTITY_VERSION}\0{check}\0{root}\0".encode())
    digest.update(_tree_identity(source, root).encode())
    python_executable, libraries, import_paths, resolved_tools = _python_environment(root)
    git_core = Path(_run([git, "--exec-path"], cwd=root)) / "git"
    tool_paths = [
        Path(sys.executable).resolve(),
        Path(shutil.which("bash") or "/bin/bash").resolve(),
        Path(python_executable),
        root / "venv/bin/activate",
        (git_core if git_core.is_file() else Path(git)).resolve(),
    ]
    tool_paths.extend(resolved_tools)
    pyvenv = root / "venv/pyvenv.cfg"
    if pyvenv.exists():
        tool_paths.append(pyvenv)
    active_pyvenv = Path(python_executable).parent.parent / "pyvenv.cfg"
    if active_pyvenv.exists():
        tool_paths.append(active_pyvenv)
    digest.update(
        _tree_identity(sorted(set(tool_paths), key=lambda p: os.fsencode(p)), root).encode()
    )
    for library in libraries:
        digest.update(_tree_identity(_dependency_paths([library]), library).encode())
    source_root = root.resolve()
    known_libraries = {item.resolve() for item in libraries}
    for import_path in import_paths:
        if import_path == source_root or import_path in known_libraries:
            continue
        if import_path.is_dir():
            digest.update(_tree_identity(_dependency_paths([import_path]), import_path).encode())
        else:
            digest.update(_tree_identity([import_path], import_path.parent).encode())
    identity_result = subprocess.run(  # noqa: S603 -- check is a closed enum; no shell.
        _fixed_command("--identity", check),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if identity_result.returncode:
        raise UnsupportedIdentity("runner configuration could not be fingerprinted")
    digest.update(identity_result.stdout.strip().encode())
    return digest.hexdigest()


def _receipt_dir(root: Path) -> Path:
    common = Path(_run(["git", "rev-parse", "--git-common-dir"], cwd=root))
    if not common.is_absolute():
        common = (root / common).resolve()
    namespace = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:24]
    return common / "nadili-gate-evidence" / namespace


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _signed_bytes(payload: dict[str, object], key: bytes) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(key, body, hashlib.sha256).hexdigest()
    return json.dumps({"payload": payload, "signature": signature}, sort_keys=True).encode()


def _read_hit(receipt: Path, key_path: Path, check: str, identity: str) -> bool:
    try:
        if receipt.is_symlink() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
            return False
        if stat.S_IMODE(receipt.stat().st_mode) != 0o600:
            return False
        if key_path.is_symlink() or key_path.stat().st_size != 32:
            return False
        if stat.S_IMODE(key_path.stat().st_mode) != 0o600:
            return False
        key = key_path.read_bytes()
        with receipt.open("rb") as source:
            raw_receipt = source.read(MAX_RECEIPT_BYTES + 1)
        if len(raw_receipt) > MAX_RECEIPT_BYTES:
            return False
        envelope = json.loads(raw_receipt)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            return False
        payload = envelope["payload"]
        if not isinstance(payload, dict) or not isinstance(envelope["signature"], str):
            return False
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(key, body, hashlib.sha256).hexdigest()
        return (
            hmac.compare_digest(signature, envelope["signature"])
            and payload == {"version": RECEIPT_VERSION, "check": check, "identity": identity}
            and len(key) == 32
        )
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def _record(receipt: Path, key_path: Path, check: str, identity: str) -> None:
    if key_path.exists():
        key = key_path.read_bytes()
        if len(key) != 32 or stat.S_IMODE(key_path.stat().st_mode) != 0o600:
            raise ValueError("evidence key has unsafe permissions or format")
    else:
        key = os.urandom(32)
        _write_private(key_path, key)
    payload: dict[str, object] = {"version": RECEIPT_VERSION, "check": check, "identity": identity}
    _write_private(receipt, _signed_bytes(payload, key))


def _fixed_command(*arguments: str) -> list[str]:
    # Git adds its exec-path and, on macOS, SDK defaults to hook environments. Run both
    # probes and checks in the same Git-established environment even outside a hook.
    return [
        "git",
        "-c",
        "alias.nadili-evidence-check=!bash scripts/python-check.sh",
        "nadili-evidence-check",
        *arguments,
    ]


def _runner(root: Path, check: str) -> list[Command]:
    return [Command(check, tuple(_fixed_command(check)))]


def _run_cached_check(root: Path, check: str, *, fresh: bool = False) -> int:
    try:
        before: str | None = input_identity(root, check)
    except (UnsupportedIdentity, OSError):
        before = None
    try:
        directory = _receipt_dir(root)
        receipt, key = directory / f"{check}.json", directory / "key"
        storage_available = True
    except (OSError, ValueError, subprocess.CalledProcessError):
        receipt = key = Path("/") / "__gate_evidence_unavailable__"
        storage_available = False
    if (
        storage_available
        and not fresh
        and before is not None
        and _read_hit(receipt, key, check, before)
    ):
        try:
            unchanged = input_identity(root, check) == before
        except (UnsupportedIdentity, OSError):
            unchanged = False
        if unchanged:
            print(f"evidence hit: {check}")
            return 0
    if storage_available and receipt.exists():
        try:
            receipt.unlink()
        except OSError as exc:
            raise ValueError("could not invalidate prior evidence") from exc
    print(f"evidence miss: {check}")
    for command in _runner(root, check):
        print(f"running: {command.label}", flush=True)
        result = subprocess.run(command.argv, cwd=root)  # noqa: S603 -- fixed runner command.
        if result.returncode:
            return result.returncode
    try:
        after = input_identity(root, check)
    except (UnsupportedIdentity, OSError):
        after = None
    if before is None or before != after or not storage_available:
        if before is not None and before != after:
            print("evidence refused: inputs changed while the check ran", file=sys.stderr)
            return 1
        return 0
    try:
        _record(receipt, key, check, before)
    except (OSError, ValueError):
        print("evidence not stored: local receipt storage unavailable", file=sys.stderr)
    return 0


def run_check(root: Path, check: str, *, fresh: bool = False) -> int:
    result = _run_cached_check(root, check, fresh=fresh)
    if result or check != "python-static":
        return result
    # Boundary policy also consumes ignored TypeScript and empty retired directories.
    # Keep this inexpensive check fresh rather than approximate those traversal inputs.
    print("running fresh: import boundaries", flush=True)
    return subprocess.run(  # noqa: S603 -- fixed boundary checker, no shell.
        [str(root / "venv/bin/python"), "scripts/check_boundaries.py"], cwd=root
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--check", required=True, choices=sorted(CHECKS))
    run_parser.add_argument("--fresh", action="store_true")
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--head")
    snapshot_parser.add_argument("--tree")
    snapshot_parser.add_argument("--print-tree", action="store_true")
    args = parser.parse_args()
    root = repository_root()
    try:
        if args.command == "snapshot":
            tree = assert_clean_snapshot(root, args.head, args.tree)
            print(tree if args.print_tree else "snapshot clean")
            return 0
        return run_check(root, args.check, fresh=args.fresh)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"gate evidence: failed safely ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
