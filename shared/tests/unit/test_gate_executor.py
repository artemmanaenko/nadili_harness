"""Behavioral tests for the private machine-wide heavy execution lease."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts import gate_executor as gate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = PROJECT_ROOT / "scripts" / "gate_executor.py"
pytestmark = pytest.mark.harness


def run_executor(
    lock_root: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    child_environment = os.environ.copy()
    if environment is not None:
        child_environment.update(environment)
    return subprocess.run(  # noqa: S603 — checked-in executor with fixed test argv.
        [sys.executable, str(EXECUTOR), *arguments],
        cwd=PROJECT_ROOT,
        env=child_environment,
        text=True,
        capture_output=True,
        check=False,
    )


#: Ceiling on child start-up, not on any behaviour under test.
PROCESS_START_BUDGET_SECONDS = 30


def start_holder(
    lock_root: Path,
    release: Path,
    *,
    label: str = "holder",
    stderr: Path | None = None,
) -> subprocess.Popen[str]:
    """Hold the lease until the test creates ``release``.

    A holder that sleeps for a fixed fraction of a second lets a slow host end the hold before
    the test observes it; a released holder keeps the asserted state until the test is done.
    """
    command = f"until [ -e {shlex.quote(str(release))} ]; do sleep 0.01; done"
    stderr_stream = None if stderr is None else stderr.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(  # noqa: S603 — checked-in executor with fixed test argv.
            [
                sys.executable,
                str(EXECUTOR),
                "run",
                "--lock-root",
                str(lock_root),
                "--label",
                label,
                "--timeout",
                str(PROCESS_START_BUDGET_SECONDS),
                "--",
                "/bin/sh",
                "-c",
                command,
            ],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if stderr_stream is None else stderr_stream,
        )
    finally:
        if stderr_stream is not None:
            stderr_stream.close()


def wait_for_holder(lock_root: Path, holder: subprocess.Popen[str]) -> None:
    holder_path = lock_root / "heavy-holder.json"
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    while not holder_path.is_file():
        assert holder.poll() is None
        assert time.monotonic() < deadline
        time.sleep(0.01)


def wait_for_output(path: Path, text: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    while text not in path.read_text(encoding="utf-8"):
        assert process.poll() is None, path.read_text(encoding="utf-8")
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_heavy_executor_allows_exactly_one_process_at_a_time(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    release = tmp_path / "release"
    second_log = tmp_path / "second.log"
    first = start_holder(lock_root, release, label="first")
    try:
        wait_for_holder(lock_root, first)
        second = start_holder(lock_root, release, label="second", stderr=second_log)
        try:
            # The second contender is observably blocked while the first still holds the lease.
            wait_for_output(second_log, "waiting for heavy lease", second)
            assert "holder=first" in second_log.read_text(encoding="utf-8")
            assert first.poll() is None
        finally:
            release.touch()
            second_result = second.communicate(timeout=PROCESS_START_BUDGET_SECONDS)
    finally:
        release.touch()
        first_result = first.communicate(timeout=PROCESS_START_BUDGET_SECONDS)

    assert first_result[0] == ""
    assert second_result[0] == ""
    assert first.returncode == 0
    assert second.returncode == 0


def test_cheap_work_can_run_while_heavy_lease_is_held(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    release = tmp_path / "release"
    holder = start_holder(lock_root, release)
    try:
        wait_for_holder(lock_root, holder)
        cheap = subprocess.run(  # noqa: S603 — fixed, non-gate local process.
            [sys.executable, "-c", "print('cheap')"],
            capture_output=True,
            text=True,
            check=False,
            timeout=PROCESS_START_BUDGET_SECONDS,
        )
        # Cheap work finished while the holder could not have released the lease.
        assert holder.poll() is None
        assert (lock_root / "heavy-holder.json").is_file()
    finally:
        release.touch()
        holder.communicate(timeout=PROCESS_START_BUDGET_SECONDS)

    assert cheap.returncode == 0
    assert cheap.stdout.strip() == "cheap"


def test_nested_executor_reuses_the_verified_inherited_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    nested = (
        f"{sys.executable} {EXECUTOR} run --lock-root {lock_root} --label nested "
        "--timeout 0 -- /bin/sh -c 'printf nested'"
    )
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "outer",
        "--timeout",
        "2",
        "--",
        "/bin/sh",
        "-c",
        nested,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "nested"


def test_heavy_executor_times_out_nonzero_with_resource_busy(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    release = tmp_path / "release"
    holder = start_holder(lock_root, release)
    try:
        wait_for_holder(lock_root, holder)
        # The holder keeps the lease until released, so this short wait expires whatever the
        # host load: expiry needs no progress from any child.
        result = run_executor(
            lock_root,
            "run",
            "--lock-root",
            str(lock_root),
            "--label",
            "waiter",
            "--timeout",
            "0.1",
            "--",
            "/bin/sh",
            "-c",
            "exit 0",
        )
    finally:
        release.touch()
        holder.communicate(timeout=PROCESS_START_BUDGET_SECONDS)

    assert result.returncode == 75
    assert "resource_busy" in result.stderr
    assert "holder=holder" in result.stderr


def test_interrupted_executor_releases_the_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    release = tmp_path / "release"
    holder = start_holder(lock_root, release)
    try:
        wait_for_holder(lock_root, holder)
        holder.send_signal(signal.SIGINT)
        assert holder.wait(timeout=PROCESS_START_BUDGET_SECONDS) == 130
    finally:
        release.touch()
        holder.communicate(timeout=PROCESS_START_BUDGET_SECONDS)

    # The interrupted owner released the lease before exiting, so the retry needs no wait.
    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "0",
        "--",
        "/bin/sh",
        "-c",
        "exit 0",
    )

    assert retry.returncode == 0, retry.stderr


def test_descendant_lifetime_keeps_lease_until_cleanup(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    first = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "descendant",
        "--timeout",
        "2",
        "--",
        "/bin/sh",
        "-c",
        "sleep 0.5 & exit 0",
    )
    assert first.returncode == 0, first.stderr

    # The first run returns only after its descendant is gone, so the lease is already free.
    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "0",
        "--",
        "/bin/sh",
        "-c",
        "exit 0",
    )
    assert retry.returncode == 0, retry.stderr


def test_inherited_environment_without_descriptor_cannot_grant_ownership(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    lock_root.mkdir(mode=0o700)
    (lock_root / "heavy.lock").touch(mode=0o600)
    result = run_executor(
        lock_root,
        "verify-inherited",
        "--lock-root",
        str(lock_root),
        environment={
            "NADILI_GATE_LEASE_FD": "9",
            "NADILI_GATE_LEASE_TOKEN": "forged",
        },
    )

    assert result.returncode != 0
    assert "inherited heavy lease" in result.stderr


def test_stale_lease_environment_is_ignored_for_a_private_lock_root(tmp_path: Path) -> None:
    """A test helper may choose a private root below a qualification subprocess.

    The helper inherits lease metadata, but not the close-on-exec descriptor.  That stale
    metadata must not poison the independent root; it must also not be accepted as authority for
    the root that actually owns the lease.
    """
    lock_root = tmp_path / "private-lease"
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "private-helper",
        "--timeout",
        "0",
        "--",
        "/bin/sh",
        "-c",
        "printf private",
        environment={
            "NADILI_GATE_LEASE_ROOT": str(tmp_path / "outer-lease"),
            "NADILI_GATE_LEASE_FD": "9",
            "NADILI_GATE_LEASE_TOKEN": "stale",
            "NADILI_GATE_LEASE_OWNER": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "private"


def test_lost_lease_descriptor_remains_fail_closed_for_the_owned_root(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "must-fail-closed",
        "--timeout",
        "0",
        "--",
        "/bin/sh",
        "-c",
        "exit 0",
        environment={
            "NADILI_GATE_LEASE_ROOT": str(lock_root),
            "NADILI_GATE_LEASE_FD": "9",
            "NADILI_GATE_LEASE_TOKEN": "stale",
        },
    )

    assert result.returncode == 2
    assert "inherited heavy lease descriptor is unavailable" in result.stderr


def test_unrelated_descriptor_cannot_masquerade_as_the_lease_owner(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    release = tmp_path / "release"
    holder = start_holder(lock_root, release)
    holder_path = lock_root / "heavy-holder.json"
    try:
        wait_for_holder(lock_root, holder)
        holder_record = json.loads(holder_path.read_text(encoding="utf-8"))
        assert isinstance(holder_record, dict)
        holder_token = holder_record.get("token")
        assert isinstance(holder_token, str)
        unrelated_descriptor = os.open(lock_root / "heavy.lock", os.O_RDWR)
        try:
            result = subprocess.run(  # noqa: S603 — checked-in executor with fixed test argv.
                [
                    sys.executable,
                    str(EXECUTOR),
                    "verify-inherited",
                    "--lock-root",
                    str(lock_root),
                ],
                cwd=PROJECT_ROOT,
                env=os.environ
                | {
                    "NADILI_GATE_LEASE_FD": str(unrelated_descriptor),
                    "NADILI_GATE_LEASE_TOKEN": holder_token,
                },
                pass_fds=(unrelated_descriptor,),
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            os.close(unrelated_descriptor)
        assert result.returncode != 0
        assert "supplied descriptor" in result.stderr
    finally:
        release.touch()
        holder.communicate(timeout=PROCESS_START_BUDGET_SECONDS)


def test_bounded_rehearsal_records_queue_duration_and_memory(tmp_path: Path) -> None:
    output = tmp_path / "rehearsal.json"
    result = run_executor(
        tmp_path / "lease",
        "rehearse",
        "--lock-root",
        str(tmp_path / "lease"),
        "--output",
        str(output),
        "--runs",
        "2",
        "--timeout",
        "2",
        "--",
        "/bin/sh",
        "-c",
        "sleep 0.05",
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert len(evidence["attempts"]) == 2
    assert all(attempt["duration_ms"] is not None for attempt in evidence["attempts"])


@pytest.mark.parametrize("exit_status", [0, 7])
def test_run_deadline_preserves_completed_command_status(tmp_path: Path, exit_status: int) -> None:
    lock_root = tmp_path / "lease"
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "completed",
        "--run-timeout",
        "5",
        "--",
        "/bin/sh",
        "-c",
        f"exit {exit_status}",
    )
    assert result.returncode == exit_status, result.stderr
    assert not (lock_root / "heavy-holder.json").exists()


def test_run_deadline_stops_descendant_before_releasing_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    ready = tmp_path / "ready"
    stopped = tmp_path / "stopped"
    child_code = (
        "import signal,time; from pathlib import Path; "
        f"signal.signal(signal.SIGTERM, lambda *_: (Path({str(stopped)!r}).touch(), exit(0))); "
        f"Path({str(ready)!r}).touch(); time.sleep(60)"
    )
    command = tmp_path / "command.py"
    command.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"while not __import__('pathlib').Path({str(ready)!r}).exists(): time.sleep(.01)\n"
        "time.sleep(60)\n"
    )
    # The run deadline starts when the lease is identified, before any child starts, so no
    # readiness signal can order expiry after the descendant is ready. Two interpreter start-ups
    # must fit inside it; the test-chosen 5 s leaves room for a loaded host, and the contract
    # (descendants stop before the lease is released) does not depend on the value.
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "forgotten-runtime",
        "--run-timeout",
        "5",
        "--",
        sys.executable,
        str(command),
    )
    assert result.returncode == 124, result.stderr
    assert "run_timeout" in result.stderr
    assert ready.exists()
    assert stopped.exists()
    assert not (lock_root / "heavy-holder.json").exists()
    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "0",
        "--",
        "/bin/sh",
        "-c",
        "exit 0",
    )
    assert retry.returncode == 0, retry.stderr


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_run_deadline_rejects_nonpositive_or_nonfinite_duration(
    tmp_path: Path, timeout: str
) -> None:
    lock_root = tmp_path / "lease"
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "invalid",
        "--run-timeout",
        timeout,
        "--",
        "/bin/sh",
        "-c",
        "exit 0",
    )
    assert result.returncode == 2
    assert "positive finite" in result.stderr
    assert not (lock_root / "heavy-holder.json").exists()


@pytest.mark.parametrize("partial_output", [False, True])
def test_streamed_command_deadline_cannot_be_blocked_by_output(
    tmp_path: Path, partial_output: bool
) -> None:
    probe = tmp_path / "probe.py"
    if partial_output:
        # The partial line must already be in the pipe when the deadline starts, otherwise a slow
        # interpreter start-up silently turns this case into the silent one. The streaming loop
        # therefore starts its deadline once the child reports that it wrote the partial line.
        ready = tmp_path / "partial-written"
        code = (
            "import time; from pathlib import Path; print('partial', end='', flush=True); "
            f"Path({str(ready)!r}).touch(); time.sleep(60)"
        )
        supervision = (
            "import time\nfrom pathlib import Path\n"
            "stream = gate._stream_supervised_output\n"
            "def after_partial_output(process, callback, deadline):\n"
            f"    while not Path({str(ready)!r}).exists(): time.sleep(.01)\n"
            "    return stream(process, callback, time.monotonic() + .2)\n"
            "gate._stream_supervised_output = after_partial_output\n"
            f"status = gate.run_supervised([sys.executable, '-c', {code!r}], "
            "run_timeout=60, output_callback=lambda line: print(line, end=''))\n"
        )
    else:
        # A silent child never finishes, so this short deadline needs no child progress.
        supervision = (
            "status = gate.run_supervised([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "run_timeout=.2, output_callback=lambda line: print(line, end=''))\n"
        )
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from scripts import gate_executor as gate\n"
        f"{supervision}"
        "print('expired after cleanup')\n"
        "raise SystemExit(status)\n"
    )
    result = subprocess.run(  # noqa: S603 — disposable probe with fixed script argv.
        [sys.executable, str(probe)],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 124, result.stderr
    assert "expired after cleanup" in result.stdout


def test_streamed_completed_command_cleans_up_pipe_holding_descendant(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from scripts import gate_executor as gate\n"
        "raise SystemExit(gate.run_supervised("
        "['/bin/sh', '-c', 'printf finished; sleep 60 & exit 0'], "
        "run_timeout=10, output_callback=lambda line: print(line, end='')))\n"
    )
    result = subprocess.run(  # noqa: S603 — disposable probe with fixed script argv.
        [sys.executable, str(probe)],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "finished"


def test_nested_command_deadline_preserves_outer_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    nested = tmp_path / "nested.sh"
    nested.write_text(
        f'"{sys.executable}" "{EXECUTOR}" run --lock-root "{lock_root}" '
        '--label inner --run-timeout .2 -- /bin/sh -c "sleep 60"\n'
        "status=$?\n"
        '[ "$status" = 124 ] || exit 9\n'
        f'"{sys.executable}" "{EXECUTOR}" verify-inherited --lock-root "{lock_root}"\n'
    )
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "outer",
        "--run-timeout",
        "15",
        "--",
        "/bin/sh",
        str(nested),
    )
    assert result.returncode == 0, result.stderr
    assert not (lock_root / "heavy-holder.json").exists()


def test_rehearsal_command_deadline_is_forwarded_to_every_contender(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    output = tmp_path / "result.json"
    result = run_executor(
        lock_root,
        "rehearse",
        "--lock-root",
        str(lock_root),
        "--output",
        str(output),
        "--runs",
        "2",
        "--run-timeout",
        ".2",
        "--",
        "/bin/sh",
        "-c",
        "sleep 60",
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text())
    assert [attempt["exit_status"] for attempt in report["attempts"]] == [124, 124]
    assert not (lock_root / "heavy-holder.json").exists()


def test_rehearsal_drains_saturated_stderr_until_timeout(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    output = tmp_path / "result.json"
    drained = tmp_path / "one-mebibyte-drained"
    # The child writes 1 MiB to stderr, far past a pipe buffer, before marking it, and keeps
    # stderr saturated until the deadline. The marker exists only if the rehearsal drained the
    # pipe. That progress must precede expiry, and the deadline starts before the child does,
    # so the test-chosen 5 s leaves room for a loaded host; the contract does not depend on it.
    result = run_executor(
        lock_root,
        "rehearse",
        "--lock-root",
        str(lock_root),
        "--output",
        str(output),
        "--runs",
        "1",
        "--run-timeout",
        "5",
        "--",
        "/bin/sh",
        "-c",
        f"yes noise | head -c 1048576 >&2 && : > {shlex.quote(str(drained))} && exec yes noise >&2",
    )
    assert result.returncode == 1, result.stderr
    assert drained.exists()
    assert json.loads(output.read_text())["attempts"][0]["exit_status"] == 124
    assert "run_timeout" in result.stderr
    assert len(result.stderr) < 5000
    assert not (lock_root / "heavy-holder.json").exists()


def test_outer_deadline_stops_term_resistant_nested_group_before_unlock(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    pid_file = tmp_path / "pid"
    code = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    # The pid file is the readiness signal, but the outer deadline starts at lease identification,
    # before the nested executor and its child start. The test-chosen 5 s covers both
    # interpreter start-ups on a loaded host; the contract (a TERM-resistant nested group is
    # stopped before the lease is released) does not depend on the value.
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "outer",
        "--run-timeout",
        "5",
        "--",
        sys.executable,
        str(EXECUTOR),
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "inner",
        "--",
        sys.executable,
        "-c",
        code,
    )
    assert result.returncode == 124, result.stderr
    process_id = int(pid_file.read_text())
    assert not (lock_root / "heavy-holder.json").exists()
    competitor = (
        "import subprocess; "
        f"result=subprocess.run(['/bin/ps','-o','stat=','-p',{str(process_id)!r}], "
        "capture_output=True,text=True); "
        "raise SystemExit(9 if any(not row.strip().startswith('Z') "
        "for row in result.stdout.splitlines() if row.strip()) else 0)"
    )
    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "0",
        "--",
        sys.executable,
        "-c",
        competitor,
    )
    assert retry.returncode == 0, retry.stderr
    assert not list((lock_root / "heavy-process-groups").glob("*/group-*"))


def test_nested_default_inherits_explicit_longer_owner_deadline(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    probe = tmp_path / "inner.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from scripts import gate_executor as gate\n"
        "gate.DEFAULT_RUN_SECONDS = .1\n"
        "raise SystemExit(gate.main(sys.argv[1:]))\n"
    )
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "outer",
        "--run-timeout",
        "5",
        "--",
        sys.executable,
        str(probe),
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "inner",
        "--",
        "/bin/sh",
        "-c",
        "sleep .3",
    )
    assert result.returncode == 0, result.stderr


def test_completed_output_is_drained_after_cleanup_crosses_command_deadline(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from scripts import gate_executor as gate\n"
        "stream = gate._stream_supervised_output\n"
        "def after_exit(process, callback, deadline):\n"
        "    process.wait()\n"
        "    return stream(process, callback, deadline - 5)\n"
        "gate._stream_supervised_output = after_exit\n"
        "raise SystemExit(gate.run_supervised("
        "['/bin/sh', '-c', \"printf 'Image vulnerability scan skipped locally: test\\\\n'\"], "
        "run_timeout=5, output_callback=lambda line: print(line, end='')))\n"
    )
    result = subprocess.run(  # noqa: S603 — disposable fixed output-draining probe.
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Image vulnerability scan skipped locally: test\n"


@pytest.mark.parametrize("interrupt", [signal.SIGINT, signal.SIGTERM])
def test_owner_interrupt_during_pending_cleanup_retains_lease_until_confirmation(
    tmp_path: Path, interrupt: int
) -> None:
    lock_root = tmp_path / "lease"
    ready = tmp_path / "cleanup-ready"
    confirmed = tmp_path / "confirmed"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from scripts import gate_executor as gate\n"
        f"root=Path({str(lock_root)!r})\n"
        f"ready=Path({str(ready)!r}); confirmed=Path({str(confirmed)!r})\n"
        "gate.PROCESS_CLEANUP_SECONDS=.05\n"
        "gate._signal_group=lambda group, sig: not confirmed.exists()\n"
        "def observe(group):\n"
        "    ready.touch()\n"
        "    return not confirmed.exists()\n"
        "gate._group_has_live_members=observe\n"
        "lease=gate.acquire_lease(root, label='pending-cleanup')\n"
        "registry=gate._lease_group_registry(root, lease.identity.token)\n"
        "registry.mkdir(parents=True)\n"
        "admitted='1792081893.049623'\n"
        "gate._process_start_time=lambda process_id: admitted\n"
        "gate._write_group_entry(registry/'group-2147483646', admitted)\n"
        "try:\n"
        "    with gate._interrupt_handlers(): lease.release()\n"
        "except gate.GateInterrupted:\n"
        "    raise SystemExit(130)\n"
    )
    owner = subprocess.Popen(  # noqa: S603 — controlled pending-observation probe.
        [sys.executable, str(probe)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
        while not ready.exists():
            assert owner.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        owner.send_signal(interrupt)
        competitor = run_executor(
            lock_root,
            "run",
            "--lock-root",
            str(lock_root),
            "--label",
            "competitor",
            "--timeout",
            "0",
            "--",
            "/bin/sh",
            "-c",
            "exit 0",
        )
        assert competitor.returncode == 75, competitor.stderr
        assert owner.poll() is None
        confirmed.touch()
        owner.communicate(timeout=PROCESS_START_BUDGET_SECONDS)
        assert owner.returncode == 130
        assert not (lock_root / "heavy-holder.json").exists()
        retry = run_executor(
            lock_root,
            "run",
            "--lock-root",
            str(lock_root),
            "--label",
            "retry",
            "--timeout",
            "0",
            "--",
            "/bin/sh",
            "-c",
            "exit 0",
        )
        assert retry.returncode == 0, retry.stderr
    finally:
        confirmed.touch()
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=PROCESS_START_BUDGET_SECONDS)


#: A start time no live process has: the recorded leader of a group id that was since recycled.
REPLACED_LEADER_START = "1.000000"


def register_group(lease: gate.HeavyLease, process_group: int, leader_started: str) -> Path:
    """Write the entry that a nested supervisor leaves behind when it crashes."""
    registry = gate._lease_group_registry(lease.lock_root, lease.identity.token)
    registry.mkdir(mode=0o700, parents=True, exist_ok=True)
    entry = registry / f"group-{process_group}"
    entry.write_text(json.dumps({"leader_started": leader_started}), encoding="utf-8")
    return entry


def start_session(
    command: list[str], *, environment: dict[str, str] | None = None, capture: bool = False
) -> subprocess.Popen[str]:
    """Start a process group of our own; tests stop it by its recorded pid only."""
    return subprocess.Popen(  # noqa: S603 — fixed test argv.
        command,
        start_new_session=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        env=None if environment is None else os.environ | environment,
    )


def start_leaderless_group(
    member: str, *, ready: Path | None = None, environment: dict[str, str] | None = None
) -> tuple[int, int]:
    """Return (pgid, member pid) of a live group whose leader has already exited.

    ``ready`` is a file the member creates once it runs its own program; until then it may still
    be the forked shell, whose environment macOS does not show.
    """
    leader = start_session(
        ["/bin/sh", "-c", f"{member} >/dev/null 2>&1 & echo $!"],
        environment=environment,
        capture=True,
    )
    assert leader.stdout is not None
    member_pid = int(leader.stdout.readline())
    leader.communicate(timeout=PROCESS_START_BUDGET_SECONDS)
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    while ready is not None and not ready.exists():
        assert is_running(member_pid)
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return leader.pid, member_pid


def is_running(process_id: int) -> bool:
    result = subprocess.run(  # noqa: S603 — fixed ps argv for one test-owned pid.
        ["/bin/ps", "-o", "stat=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
    return any(not row.startswith("Z") for row in rows)


def test_teardown_skips_stale_group_whose_pgid_now_belongs_to_unrelated_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unrelated = start_session(["/bin/sleep", "300"])
    try:
        lease = gate.acquire_lease(tmp_path / "lease", label="stale-entry")
        entry = register_group(lease, unrelated.pid, REPLACED_LEADER_START)
        lease.release()
        assert unrelated.poll() is None
        assert is_running(unrelated.pid)
        assert not entry.parent.exists()
        skipped = f"process_group_skipped group={unrelated.pid} reason=leader_replaced"
        assert skipped in capsys.readouterr().err
    finally:
        unrelated.kill()
        unrelated.wait(timeout=PROCESS_START_BUDGET_SECONDS)


def test_teardown_stops_registered_group_whose_leader_is_unchanged(tmp_path: Path) -> None:
    admitted = start_session(["/bin/sleep", "300"])
    # Reap concurrently, as a live parent would, so the stopped group does not linger as a zombie.
    reaper = threading.Thread(target=admitted.wait, daemon=True)
    reaper.start()
    try:
        started = gate._process_start_time(admitted.pid)
        assert started is not None
        lease = gate.acquire_lease(tmp_path / "lease", label="crashed-supervisor")
        entry = register_group(lease, admitted.pid, started)
        lease.release()
        reaper.join(timeout=PROCESS_START_BUDGET_SECONDS)
        assert admitted.returncode == -signal.SIGTERM
        assert not entry.parent.exists()
    finally:
        if admitted.poll() is None:
            admitted.kill()
            admitted.wait(timeout=PROCESS_START_BUDGET_SECONDS)


def test_teardown_skips_leaderless_group_without_lease_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    process_group, member = start_leaderless_group("/bin/sleep 300")
    try:
        lease = gate.acquire_lease(tmp_path / "lease", label="leaderless-unverified")
        entry = register_group(lease, process_group, REPLACED_LEADER_START)
        lease.release()
        assert is_running(member)
        assert not entry.parent.exists()
        skipped = f"process_group_skipped group={process_group} reason=leader_exited_unverified"
        assert skipped in capsys.readouterr().err
    finally:
        if is_running(member):
            os.kill(member, signal.SIGKILL)


@pytest.mark.skipif(sys.platform != "darwin", reason="reads environments with macOS ps -E")
def test_teardown_stops_leaderless_group_whose_member_carries_lease_token(tmp_path: Path) -> None:
    lease = gate.acquire_lease(tmp_path / "lease", label="leaderless-orphan")
    ready = tmp_path / "member-ready"
    code = f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(300)"
    process_group, member = start_leaderless_group(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}",
        ready=ready,
        environment={"NADILI_GATE_LEASE_TOKEN": lease.identity.token},
    )
    try:
        entry = register_group(lease, process_group, REPLACED_LEADER_START)
        lease.release()
        assert not is_running(member)
        assert not entry.parent.exists()
    finally:
        if is_running(member):
            os.kill(member, signal.SIGKILL)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"[]",
        b'{"leader_started": 5}',
        b'{"leader_started": ""}',
        b'{"leader_started": " "}',
        b'{"leader_started": "garbage"}',
        b'{"leader_started": "1792081893.049623\\n"}',
        b'{"leader_started": " 1792081893.049623"}',
        b'{"leader_started": "1792081893"}',
        b'{"leader_started": "1792081893.49623"}',
        b'{"leader_started": "1792081893.0496230"}',
        b'{"leader_started": "0.000000"}',
        b'{"leader_started": "-1.000000"}',
        b'{"leader_started": "01792081893.049623"}',
        b'{"leader_started": "1792081893792081893.049623"}',
        b"{" * 5000,
    ],
)
def test_teardown_fails_closed_on_malformed_group_identity(tmp_path: Path, payload: bytes) -> None:
    lease = gate.acquire_lease(tmp_path / "lease", label="malformed-entry")
    registry = gate._lease_group_registry(lease.lock_root, lease.identity.token)
    registry.mkdir(mode=0o700, parents=True)
    (registry / "group-2147483646").write_bytes(payload)
    try:
        with pytest.raises(gate.GateExecutorError, match="process group identity is invalid"):
            lease.release()
    finally:
        os.close(lease.file_descriptor)


def test_nested_admission_records_the_group_leader_start_time(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    # The oracle is ps, independent of the executor's own kernel reads: on macOS the recorded
    # seconds must render as the leader's lstart.
    check = (
        "import json, os, re, subprocess, sys\n"
        "from datetime import UTC, datetime\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['NADILI_GATE_LEASE_ROOT'])\n"
        "token = os.environ['NADILI_GATE_LEASE_TOKEN']\n"
        "entry = root / 'heavy-process-groups' / token / f'group-{os.getpgrp()}'\n"
        "recorded = json.loads(entry.read_text())['leader_started']\n"
        "assert os.getpgrp() == os.getpid(), 'the registered process is not the leader'\n"
        "assert re.fullmatch(r'[1-9][0-9]*\\.[0-9]{6}', recorded), recorded\n"
        "if sys.platform == 'darwin':\n"
        "    seen = subprocess.run(['/bin/ps', '-o', 'lstart=', '-p', str(os.getpid())],\n"
        "        capture_output=True, text=True, env={'LC_ALL': 'C', 'TZ': 'UTC'}).stdout\n"
        "    moment = datetime.fromtimestamp(int(recorded.split('.')[0]), UTC)\n"
        "    expected = f'{moment:%a %b} {moment.day} {moment:%H:%M:%S %Y}'\n"
        "    assert expected == ' '.join(seen.split()), (expected, seen)\n"
    )
    result = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "admission",
        "--",
        sys.executable,
        "-c",
        check,
    )
    assert result.returncode == 0, result.stderr
    assert not list((lock_root / "heavy-process-groups").glob("*/group-*"))


def test_teardown_skips_group_when_leader_status_is_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_process_id: int) -> str | None:
        raise gate.GateExecutorError("heavy process group leader status is unavailable")

    unrelated = start_session(["/bin/sleep", "300"])
    try:
        lease = gate.acquire_lease(tmp_path / "lease", label="status-unavailable")
        entry = register_group(lease, unrelated.pid, REPLACED_LEADER_START)
        monkeypatch.setattr(gate, "_process_start_time", unavailable)
        lease.release()
        assert is_running(unrelated.pid)
        assert not entry.parent.exists()
        skipped = f"process_group_skipped group={unrelated.pid} reason=status_unavailable"
        assert skipped in capsys.readouterr().err
    finally:
        unrelated.kill()
        unrelated.wait(timeout=PROCESS_START_BUDGET_SECONDS)


def test_teardown_discards_identity_left_unpublished_by_interrupted_admission(
    tmp_path: Path,
) -> None:
    lease = gate.acquire_lease(tmp_path / "lease", label="interrupted-admission")
    registry = gate._lease_group_registry(lease.lock_root, lease.identity.token)
    registry.mkdir(mode=0o700, parents=True)
    (registry / f"{gate.GROUP_ENTRY_TEMP_PREFIX}partial").write_bytes(b'{"leader_')
    lease.release()
    assert not registry.exists()


def test_teardown_tolerates_supervisor_deregistering_while_another_group_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admitted = "1792081893.049623"
    lease = gate.acquire_lease(tmp_path / "lease", label="concurrent-deregistration")
    registry = gate._lease_group_registry(lease.lock_root, lease.identity.token)
    registry.mkdir(mode=0o700, parents=True)
    for process_group in (2147483645, 2147483646):
        gate._write_group_entry(registry / f"group-{process_group}", admitted)
    stopped: list[int] = []

    def stop_while_sibling_deregisters(
        process_group: int, _process: subprocess.Popen[str] | None = None
    ) -> None:
        # While this group is being stopped, the other group's surviving supervisor finishes and
        # removes its entry under the registry guard, as run_supervised does.
        if not stopped:
            with gate._registry_guard(registry):
                for entry in registry.glob("group-*"):
                    if entry.name != f"group-{process_group}":
                        entry.unlink()
        stopped.append(process_group)

    monkeypatch.setattr(gate, "_process_start_time", lambda _process_id: admitted)
    monkeypatch.setattr(gate, "_terminate_group", stop_while_sibling_deregisters)
    lease.release()
    assert sorted(stopped) == [2147483645, 2147483646]
    assert not registry.exists()


def test_admission_is_refused_when_leader_start_time_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = gate.acquire_lease(tmp_path / "lease", label="unreadable-leader")
    try:
        for name, value in lease.child_environment({}).items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(gate, "_process_start_time", lambda _process_id: None)
        with pytest.raises(gate.GateExecutorError, match="leader is unavailable"):
            gate._exec_registered_command(["/bin/sh", "-c", "exit 0"])
        registry = gate._lease_group_registry(lease.lock_root, lease.identity.token)
        assert not list(registry.glob("group-*"))
    finally:
        lease.release()


def test_process_identity_reads_never_exec_a_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent sandboxes refuse to exec /bin/ps; admission and teardown must not depend on it."""

    def refuse(*_arguments: object, **_options: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    started = gate._process_start_time(os.getpid())
    assert started is not None
    assert gate.LEADER_START_RE.fullmatch(started)
    assert gate._process_start_time(os.getpid()) == started
    assert os.getpid() in gate._live_group_members(os.getpgrp())
    assert any(item.startswith(b"PATH=") for item in gate._process_environment(os.getpid()))
