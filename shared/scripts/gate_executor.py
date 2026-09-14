#!/usr/bin/env python3
"""Own the machine-wide heavy gate lease and supervise its process group."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LEASE_FILENAME = "heavy.lock"
HOLDER_FILENAME = "heavy-holder.json"
DEFAULT_WAIT_SECONDS = 900.0
WAIT_POLL_SECONDS = 5.0
PROCESS_CLEANUP_SECONDS = 5.0
RESOURCE_BUSY_EXIT_STATUS = 75
MAX_HOLDER_BYTES = 4096
MAX_REHEARSAL_RUNS = 3


class GateExecutorError(RuntimeError):
    """Raised when the heavy lease cannot be safely acquired or released."""


class ResourceBusyError(GateExecutorError):
    """Raised when the bounded heavy-lease wait expires."""


class GateInterrupted(GateExecutorError):
    """Raised when the lease owner is interrupted while supervising a child."""


@dataclass(frozen=True, slots=True)
class LeaseIdentity:
    """Non-secret holder information written for blocked operators."""

    token: str
    owner_pid: int
    label: str


@dataclass(slots=True)
class HeavyLease:
    """An open, OS-backed lease descriptor retained by the owning process."""

    lock_root: Path
    lock_path: Path
    holder_path: Path
    file_descriptor: int
    identity: LeaseIdentity
    _released: bool = False

    def child_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        """Return a child environment carrying a verifiable descriptor authority."""

        environment = dict(base)
        environment["NADILI_GATE_LOCK_DIR"] = str(self.lock_root)
        environment["NADILI_GATE_LEASE_FD"] = str(self.file_descriptor)
        environment["NADILI_GATE_LEASE_ROOT"] = str(self.lock_root)
        environment["NADILI_GATE_LEASE_TOKEN"] = self.identity.token
        environment["NADILI_GATE_LEASE_OWNER"] = "1"
        return environment

    def __enter__(self) -> HeavyLease:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object | None,
    ) -> None:
        self.release()

    def release(self) -> None:
        """Release the lock only after the caller has stopped its heavy descendants."""

        if self._released:
            return
        try:
            _remove_holder_if_owned(self.holder_path, self.identity.token)
            fcntl.flock(self.file_descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            raise GateExecutorError("heavy lease could not be released") from exc
        finally:
            os.close(self.file_descriptor)
            self._released = True


def _validate_lock_root(lock_root: Path) -> Path:
    if lock_root.exists() and lock_root.is_symlink():
        raise GateExecutorError("heavy lease root must not be a symlink")
    try:
        lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_stat = lock_root.stat()
    except OSError as exc:
        raise GateExecutorError("heavy lease root could not be created") from exc
    if not stat.S_ISDIR(lock_stat.st_mode) or lock_root.is_symlink():
        raise GateExecutorError("heavy lease root must be a directory")
    if lock_stat.st_uid != os.geteuid():
        raise GateExecutorError("heavy lease root must be owned by the current user")
    if stat.S_IMODE(lock_stat.st_mode) & 0o077:
        # mkdir(mode=..., exist_ok=True) does not re-apply the mode to a directory that already
        # exists, and this root predates the lease -- the shell library has been creating it for
        # the gate-run census and version reservations since long before heavy.lock lived here.
        # On this machine it is 0755. Refusing outright would be correct only if we could not fix
        # it; we own it, so tighten it instead. Failing here instead disables every heavy gate leg
        # on the host until someone chmods a temp directory by hand.
        try:
            lock_root.chmod(0o700)
        except OSError as exc:
            raise GateExecutorError(
                "heavy lease root is group or world accessible and could not be tightened"
            ) from exc
    return lock_root


def _open_lock(lock_root: Path) -> tuple[Path, int]:
    root = _validate_lock_root(lock_root)
    lock_path = root / LEASE_FILENAME
    open_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(lock_path, open_flags, 0o600)
        lock_stat = os.fstat(file_descriptor)
        path_stat = lock_path.stat()
    except OSError as exc:
        raise GateExecutorError("heavy lease lock could not be opened") from exc
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_dev != path_stat.st_dev
        or lock_stat.st_ino != path_stat.st_ino
        or lock_stat.st_uid != os.geteuid()
        or stat.S_IMODE(lock_stat.st_mode) & 0o077
    ):
        os.close(file_descriptor)
        raise GateExecutorError("heavy lease lock has unsafe ownership or permissions")
    return lock_path, file_descriptor


def _holder_description(holder_path: Path) -> str:
    try:
        raw = holder_path.read_bytes()
    except OSError:
        return "unknown"
    if len(raw) > MAX_HOLDER_BYTES:
        return "unknown"
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(value, dict):
        return "unknown"
    label = value.get("label")
    owner_pid = value.get("owner_pid")
    host = value.get("host")
    if not isinstance(label, str) or not isinstance(owner_pid, int) or not isinstance(host, str):
        return "unknown"
    return f"{label} pid={owner_pid} host={host}"


def _write_holder(holder_path: Path, identity: LeaseIdentity) -> None:
    payload = {
        "label": identity.label,
        "owner_pid": identity.owner_pid,
        "host": socket.gethostname()[:128],
        "started_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "token": identity.token,
    }
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".heavy-holder.", dir=holder_path.parent, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as holder_file:
                json.dump(payload, holder_file, sort_keys=True, separators=(",", ":"))
                holder_file.write("\n")
            file_descriptor = -1
            os.replace(temporary_path, holder_path)
        except BaseException:
            if file_descriptor >= 0:
                with suppress(OSError):
                    os.close(file_descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise GateExecutorError("heavy lease holder identity could not be written") from exc


def _remove_holder_if_owned(holder_path: Path, token: str) -> None:
    try:
        raw = holder_path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if isinstance(value, dict) and value.get("token") == token:
        try:
            holder_path.unlink(missing_ok=True)
        except OSError as exc:
            raise GateExecutorError("heavy lease holder identity could not be removed") from exc


def _wait_for_lock(
    file_descriptor: int,
    holder_path: Path,
    timeout: float,
    label: str,
) -> None:
    deadline = time.monotonic() + timeout
    waited = 0.0
    while True:
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if waited:
                print(
                    f"gate_executor: acquired heavy lease for {label} after {waited:.0f}s",
                    file=sys.stderr,
                )
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                holder = _holder_description(holder_path)
                raise ResourceBusyError(
                    f"resource_busy: heavy lease wait exceeded {timeout:.0f}s; holder={holder}"
                ) from None
            if waited == 0 or waited % 60 == 0:
                print(
                    "gate_executor: waiting for heavy lease "
                    f"({waited:.0f}/{timeout:.0f}s, holder={_holder_description(holder_path)})",
                    file=sys.stderr,
                    flush=True,
                )
            delay = min(WAIT_POLL_SECONDS, max(0.0, deadline - time.monotonic()))
            time.sleep(delay)
            waited += delay


def acquire_lease(
    lock_root: Path,
    *,
    label: str,
    timeout: float = DEFAULT_WAIT_SECONDS,
    owner_pid: int | None = None,
) -> HeavyLease:
    """Acquire the shared lease with a bounded, diagnosable wait."""

    if timeout < 0:
        raise GateExecutorError("heavy lease timeout must be non-negative")
    lock_path, file_descriptor = _open_lock(lock_root)
    holder_path = lock_path.with_name(HOLDER_FILENAME)
    try:
        _wait_for_lock(file_descriptor, holder_path, timeout, label)
        identity = LeaseIdentity(
            token=secrets.token_hex(16),
            owner_pid=os.getpid() if owner_pid is None else owner_pid,
            label=label,
        )
        _write_holder(holder_path, identity)
        return HeavyLease(lock_root, lock_path, holder_path, file_descriptor, identity)
    except BaseException:
        os.close(file_descriptor)
        raise


def _descriptor_matches_lock(file_descriptor: int, lock_root: Path) -> tuple[Path, Path]:
    lock_path, probe_descriptor = _open_lock(lock_root)
    try:
        try:
            descriptor_stat = os.fstat(file_descriptor)
        except OSError as exc:
            raise GateExecutorError("inherited heavy lease descriptor is unavailable") from exc
        lock_stat = os.stat(lock_path)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or descriptor_stat.st_dev != lock_stat.st_dev
            or descriptor_stat.st_ino != lock_stat.st_ino
        ):
            raise GateExecutorError("inherited lease descriptor does not name the heavy lock")
    finally:
        os.close(probe_descriptor)
    return lock_path, lock_path.with_name(HOLDER_FILENAME)


def _verify_descriptor_holds_lock(file_descriptor: int, lock_path: Path) -> None:
    try:
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise GateExecutorError(
            "inherited heavy lease is not held by the supplied descriptor"
        ) from exc
    except OSError as exc:
        raise GateExecutorError("inherited heavy lease descriptor could not be verified") from exc
    probe_flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        probe_flags |= os.O_NOFOLLOW
    try:
        probe_descriptor = os.open(lock_path, probe_flags)
        try:
            fcntl.flock(probe_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        finally:
            os.close(probe_descriptor)
    except OSError as exc:
        raise GateExecutorError("inherited heavy lease could not be probed") from exc
    raise GateExecutorError("inherited heavy lease is not held")


def inherited_lease_fd(environment: Mapping[str, str], lock_root: Path) -> int | None:
    """Validate descriptor, lock ownership and token; env values alone are insufficient."""

    fd_value = environment.get("NADILI_GATE_LEASE_FD")
    token = environment.get("NADILI_GATE_LEASE_TOKEN")
    if fd_value is None and token is None:
        return None
    if fd_value is None or token is None:
        raise GateExecutorError("inherited heavy lease requires both descriptor and token")
    try:
        file_descriptor = int(fd_value)
    except ValueError as exc:
        raise GateExecutorError("inherited heavy lease descriptor is invalid") from exc
    if file_descriptor < 3:
        raise GateExecutorError("inherited heavy lease descriptor is invalid")
    lock_path, holder_path = _descriptor_matches_lock(file_descriptor, lock_root)
    try:
        holder = json.loads(holder_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateExecutorError("inherited heavy lease holder identity is unavailable") from exc
    if not isinstance(holder, dict) or holder.get("token") != token:
        raise GateExecutorError("inherited heavy lease token is invalid")

    # Check the supplied open file description itself. A separate probe would only prove that
    # some process owns the lock, allowing an unrelated descriptor plus copied environment to
    # masquerade as the owner.
    _verify_descriptor_holds_lock(file_descriptor, lock_path)
    return file_descriptor


def _lease_environment_for_lock(
    environment: Mapping[str, str], lock_root: Path
) -> Mapping[str, str]:
    """Ignore copied lease markers when the caller explicitly selected another lock root.

    Test and helper subprocesses commonly choose a private lock root while running below a heavy
    gate.  Their environment still contains the parent's lease metadata, but Python's default
    ``close_fds`` means the descriptor is gone.  Treating that stale metadata as authority makes
    unrelated helpers fail before they can use their private root.  A root mismatch therefore
    strips markers after checking that an actually open descriptor does not authenticate against
    the requested root.  A missing or matching root remains fail-closed, so malformed or lost
    authority can never bypass the lease protecting the requested root.
    """

    inherited_root = environment.get("NADILI_GATE_LEASE_ROOT")
    if inherited_root is None:
        return environment
    try:
        roots_match = Path(inherited_root).resolve() == lock_root.resolve()
    except OSError:
        roots_match = True
    if roots_match:
        return environment
    try:
        inherited_lease_fd(environment, lock_root)
    except GateExecutorError:
        pass
    else:
        return environment
    sanitized = dict(environment)
    for name in (
        "NADILI_GATE_LEASE_FD",
        "NADILI_GATE_LEASE_ROOT",
        "NADILI_GATE_LEASE_TOKEN",
        "NADILI_GATE_LEASE_OWNER",
    ):
        sanitized.pop(name, None)
    return sanitized


def release_inherited_lease(lock_root: Path, file_descriptor: int, token: str) -> None:
    """Release a descriptor acquired by the shell bridge after its resources are stopped."""

    _lock_path, holder_path = _descriptor_matches_lock(file_descriptor, lock_root)
    _verify_descriptor_holds_lock(file_descriptor, _lock_path)
    _remove_holder_if_owned(holder_path, token)
    try:
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
    except OSError as exc:
        raise GateExecutorError("heavy lease could not be released") from exc


def _signal_group(process_group: int, signal_number: int) -> bool:
    """Signal a process group, reporting whether it is still there to signal.

    ProcessLookupError is the expected "already gone". PermissionError is the same answer wearing
    a different errno: once the group leader has exited, macOS can report EPERM rather than ESRCH
    for a group that still has a stale or recycled id. Treating it as a hard error made a
    successfully finished run exit 2 during best-effort teardown -- the failure this cleanup exists
    to prevent, caused by the cleanup itself.
    """

    try:
        os.killpg(process_group, signal_number)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    process_group = process.pid
    if not _signal_group(process_group, signal.SIGTERM):
        return
    deadline = time.monotonic() + PROCESS_CLEANUP_SECONDS
    while time.monotonic() < deadline:
        if not _signal_group(process_group, 0):
            return
        if process.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.05)
        time.sleep(0.05)
    if not _signal_group(process_group, signal.SIGKILL):
        return
    try:
        process.wait(timeout=PROCESS_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GateExecutorError("heavy child process group did not stop") from exc


#: Set once a signal has been delivered. Cleanup on the interrupt path touches process groups and
#: descriptors that the interrupt itself may already have torn down, so it can raise OSError over
#: the in-flight GateInterrupted and turn a clean interrupt into an opaque exit 2. The fact of the
#: interrupt is what the exit status must report; a best-effort cleanup error is reported too, but
#: on stderr, where it does not overwrite the reason.
_interrupted = False


def _interrupt_handler(_signal_number: int, _frame: object) -> None:
    global _interrupted
    _interrupted = True
    raise GateInterrupted("heavy lease owner interrupted")


@contextmanager
def _interrupt_handlers() -> Iterator[None]:
    previous_int = signal.signal(signal.SIGINT, _interrupt_handler)
    previous_term = signal.signal(signal.SIGTERM, _interrupt_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def run_supervised(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
    output_callback: Callable[[str], None] | None = None,
) -> int:
    """Run one process group and stop descendants before returning to the lease owner."""

    if not command:
        raise GateExecutorError("heavy command must not be empty")
    stdout: int | None = subprocess.PIPE if output_callback is not None else None
    with _interrupt_handlers():
        process = subprocess.Popen(  # noqa: S603 — explicit argv, never a shell.
            list(command),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdout=stdout,
            stderr=subprocess.STDOUT if output_callback is not None else None,
            text=output_callback is not None,
            bufsize=1 if output_callback is not None else -1,
            start_new_session=True,
            pass_fds=tuple(sorted(set(pass_fds))),
        )
        try:
            if output_callback is not None and process.stdout is not None:
                for line in process.stdout:
                    output_callback(line)
            return process.wait()
        except (GateInterrupted, KeyboardInterrupt):
            _terminate_process_group(process)
            raise
        finally:
            if process.poll() is None:
                _terminate_process_group(process)
            else:
                # The direct child exited, but the process group may still contain a descendant
                # that owns a browser, server, or Docker helper. Keep the lease until that group
                # is gone.
                _terminate_process_group(process)


def run_with_lease(
    command: Sequence[str],
    *,
    lock_root: Path,
    label: str,
    timeout: float,
) -> int:
    """Acquire one lease, supervise one process group, then release after cleanup."""

    started = time.monotonic()
    metrics_path = (
        Path(os.environ["NADILI_GATE_REHEARSAL_METRICS_FILE"])
        if "NADILI_GATE_REHEARSAL_METRICS_FILE" in os.environ
        else None
    )
    acquired = started
    exit_status: int | None = None
    try:
        # Arm the handlers BEFORE acquiring, not only inside run_supervised. Between acquire_lease
        # writing the holder file and the supervisor installing its own, an interrupt is handled by
        # whatever default is installed, so the owner can die still holding the lease without
        # running its release. The holder file is what a waiter watches for, which makes that
        # window exactly when an interrupt is most likely to land. run_supervised still installs
        # its own handlers; re-arming the same one is harmless.
        with _interrupt_handlers():
            lease_environment = _lease_environment_for_lock(os.environ, lock_root)
            inherited_fd = inherited_lease_fd(lease_environment, lock_root)
            if inherited_fd is not None:
                exit_status = run_supervised(
                    command,
                    environment=lease_environment,
                    pass_fds=(inherited_fd,),
                )
            else:
                with acquire_lease(lock_root, label=label, timeout=timeout) as lease:
                    acquired = time.monotonic()
                    environment = lease.child_environment(lease_environment)
                    exit_status = run_supervised(
                        command,
                        environment=environment,
                        pass_fds=(lease.file_descriptor,),
                    )
        return exit_status
    finally:
        if metrics_path is not None:
            metrics_path.write_text(
                json.dumps(
                    {
                        "started_monotonic": started,
                        "acquired_monotonic": acquired,
                        "ended_monotonic": time.monotonic(),
                        "exit_status": exit_status,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )


def _memory_rss_for_processes(process_ids: Sequence[int]) -> int:
    if not process_ids:
        return 0
    ps = shutil.which("ps")
    if ps is None:
        return 0
    try:
        result = subprocess.run(  # noqa: S603 — fixed ps inspection argv, no shell.
            [ps, "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, check=False
        )
    except OSError:
        return 0
    if result.returncode != 0:
        return 0
    rows: dict[int, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, parent_pid, rss = (int(field) for field in fields)
        except ValueError:
            continue
        rows[pid] = (parent_pid, rss)
    descendants = set(process_ids)
    changed = True
    while changed:
        changed = False
        for pid, (parent_pid, _rss) in rows.items():
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return sum(rows[pid][1] for pid in descendants if pid in rows)


def run_rehearsal(
    command: Sequence[str],
    *,
    lock_root: Path,
    output_path: Path,
    runs: int,
    timeout: float,
) -> int:
    """Run at most three bounded contenders and record queue, timing and RSS evidence."""

    if not 1 <= runs <= MAX_REHEARSAL_RUNS:
        raise GateExecutorError(f"rehearsal runs must be between 1 and {MAX_REHEARSAL_RUNS}")
    started = time.monotonic()
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metrics_directory = output_path.parent / f".{output_path.name}.metrics"
    metrics_directory.mkdir(mode=0o700, exist_ok=True)
    children: list[subprocess.Popen[bytes]] = []
    try:
        for index in range(runs):
            child = subprocess.Popen(  # noqa: S603 — re-enters this fixed executor CLI.
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "run",
                    "--lock-root",
                    str(lock_root),
                    "--timeout",
                    str(timeout),
                    "--label",
                    f"rehearsal-{index + 1}",
                    "--",
                    *command,
                ],
                env=os.environ
                | {
                    "NADILI_GATE_REHEARSAL_METRICS_FILE": str(
                        metrics_directory / f"{index + 1}.json"
                    )
                },
                stdout=subprocess.DEVNULL,
                # Keep stderr: a failed attempt with its reason discarded is a measurement that
                # cannot be acted on, and the reason is what tells an operator whether the run
                # failed for a real contention reason or never started at all.
                stderr=subprocess.PIPE,
            )
            children.append(child)
        peak_rss_kib = 0
        while any(child.poll() is None for child in children):
            peak_rss_kib = max(
                peak_rss_kib, _memory_rss_for_processes([child.pid for child in children])
            )
            time.sleep(0.25)
        statuses = []
        failures: list[str] = []
        for index, child in enumerate(children):
            stderr_output = child.stderr.read() if child.stderr is not None else b""
            statuses.append(child.wait())
            if statuses[-1] != 0:
                reason = stderr_output.decode("utf-8", errors="replace").strip().splitlines()
                failures.append(f"attempt {index + 1}: {reason[-1] if reason else 'no output'}")
    except (KeyboardInterrupt, GateInterrupted):
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait()
        raise
    ended = time.monotonic()
    attempts: list[dict[str, int | None]] = []
    for index, (_child, status) in enumerate(zip(children, statuses, strict=True)):
        metrics_path = metrics_directory / f"{index + 1}.json"
        metrics: dict[str, object] = {}
        if metrics_path.is_file():
            try:
                loaded = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                metrics = loaded
        started_monotonic = metrics.get("started_monotonic")
        acquired_monotonic = metrics.get("acquired_monotonic")
        ended_monotonic = metrics.get("ended_monotonic")
        attempts.append(
            {
                "index": index + 1,
                "queue_wait_ms": (
                    int((acquired_monotonic - started_monotonic) * 1000)
                    if isinstance(started_monotonic, (int, float))
                    and isinstance(acquired_monotonic, (int, float))
                    else None
                ),
                "duration_ms": (
                    int((ended_monotonic - acquired_monotonic) * 1000)
                    if isinstance(acquired_monotonic, (int, float))
                    and isinstance(ended_monotonic, (int, float))
                    else None
                ),
                "exit_status": status,
            }
        )
    result = {
        "schema_version": 1,
        "runs": runs,
        "wall_duration_ms": int((ended - started) * 1000),
        "peak_supervised_rss_kib": peak_rss_kib,
        "attempts": attempts,
    }
    if failures:
        result["failures"] = failures
        for failure in failures:
            print(f"gate_executor: rehearsal {failure}", file=sys.stderr)
    for metrics_path in metrics_directory.glob("*.json"):
        metrics_path.unlink(missing_ok=True)
    metrics_directory.rmdir()
    output_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0 if all(status == 0 for status in statuses) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("run", "rehearse"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--lock-root", type=Path, required=True)
        command_parser.add_argument("--timeout", type=float, default=DEFAULT_WAIT_SECONDS)
        if command == "run":
            command_parser.add_argument("--label", required=True)
        else:
            command_parser.add_argument("--output", type=Path, required=True)
            command_parser.add_argument("--runs", type=int, default=MAX_REHEARSAL_RUNS)
        command_parser.add_argument("command_args", nargs=argparse.REMAINDER)

    inherited = subparsers.add_parser("verify-inherited")
    inherited.add_argument("--lock-root", type=Path, required=True)

    acquire = subparsers.add_parser("acquire-fd")
    acquire.add_argument("--lock-root", type=Path, required=True)
    acquire.add_argument("--fd", type=int, required=True)
    acquire.add_argument("--label", required=True)
    acquire.add_argument("--owner-pid", type=int)
    acquire.add_argument("--timeout", type=float, default=DEFAULT_WAIT_SECONDS)

    release = subparsers.add_parser("release-fd")
    release.add_argument("--lock-root", type=Path, required=True)
    release.add_argument("--fd", type=int, required=True)
    release.add_argument("--token", required=True)
    return parser


def _command_args(args: argparse.Namespace) -> list[str]:
    command = list(args.command_args)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise GateExecutorError("command is required after --")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return run_with_lease(
                _command_args(args),
                lock_root=args.lock_root,
                label=args.label,
                timeout=args.timeout,
            )
        if args.command == "rehearse":
            return run_rehearsal(
                _command_args(args),
                lock_root=args.lock_root,
                output_path=args.output,
                runs=args.runs,
                timeout=args.timeout,
            )
        if args.command == "verify-inherited":
            inherited_lease_fd(os.environ, args.lock_root)
            return 0
        if args.command == "acquire-fd":
            if args.timeout < 0:
                raise GateExecutorError("heavy lease timeout must be non-negative")
            lock_path = _validate_lock_root(args.lock_root) / LEASE_FILENAME
            holder_path = lock_path.with_name(HOLDER_FILENAME)
            _descriptor_matches_lock(args.fd, args.lock_root)
            _wait_for_lock(args.fd, holder_path, args.timeout, args.label)
            identity = LeaseIdentity(
                token=secrets.token_hex(16),
                owner_pid=os.getpid() if args.owner_pid is None else args.owner_pid,
                label=args.label,
            )
            _write_holder(holder_path, identity)
            print(identity.token)
            return 0
        if args.command == "release-fd":
            release_inherited_lease(args.lock_root, args.fd, args.token)
            return 0
        raise GateExecutorError("unknown gate executor command")
    except ResourceBusyError as exc:
        print(str(exc), file=sys.stderr)
        return RESOURCE_BUSY_EXIT_STATUS
    except (GateInterrupted, KeyboardInterrupt):
        return 130
    except (GateExecutorError, OSError) as exc:
        print(f"gate_executor: {exc}", file=sys.stderr)
        return 130 if _interrupted else 2


if __name__ == "__main__":
    raise SystemExit(main())
