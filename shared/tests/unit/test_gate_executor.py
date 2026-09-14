"""Behavioral tests for the private machine-wide heavy execution lease."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


def start_sleeping_holder(lock_root: Path, seconds: str = "0.4") -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 — checked-in executor with fixed test argv.
        [
            sys.executable,
            str(EXECUTOR),
            "run",
            "--lock-root",
            str(lock_root),
            "--label",
            "holder",
            "--timeout",
            "2",
            "--",
            "/bin/sh",
            "-c",
            f"sleep {seconds}",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


#: Ceiling on child start-up, not on any behaviour under test.
PROCESS_START_BUDGET_SECONDS = 30


def wait_for_holder(lock_root: Path, holder: subprocess.Popen[str]) -> None:
    holder_path = lock_root / "heavy-holder.json"
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    while not holder_path.is_file():
        assert holder.poll() is None
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_heavy_executor_allows_exactly_one_process_at_a_time(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    first = start_sleeping_holder(lock_root)
    wait_for_holder(lock_root, first)
    second = start_sleeping_holder(lock_root)

    first_result = first.communicate(timeout=PROCESS_START_BUDGET_SECONDS)
    second_result = second.communicate(timeout=PROCESS_START_BUDGET_SECONDS)

    assert first_result[0] == ""
    assert second_result[0] == ""
    assert first.returncode == 0
    assert second.returncode == 0
    assert "waiting for heavy lease" in second_result[1]


def test_cheap_work_can_run_while_heavy_lease_is_held(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    holder = start_sleeping_holder(lock_root, "0.5")
    started = time.monotonic()
    cheap = subprocess.run(  # noqa: S603 — fixed, non-gate local process.
        [sys.executable, "-c", "print('cheap')"],
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    holder.wait(timeout=PROCESS_START_BUDGET_SECONDS)

    assert cheap.returncode == 0
    assert cheap.stdout.strip() == "cheap"
    assert elapsed < 0.4


def test_nested_executor_reuses_the_verified_inherited_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    nested = (
        f"{sys.executable} {EXECUTOR} run --lock-root {lock_root} --label nested "
        "--timeout 1 -- /bin/sh -c 'printf nested'"
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
    holder = start_sleeping_holder(lock_root, "0.5")
    wait_for_holder(lock_root, holder)

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
    holder.wait(timeout=PROCESS_START_BUDGET_SECONDS)

    assert result.returncode == 75
    assert "resource_busy" in result.stderr
    assert "holder=holder" in result.stderr


def test_interrupted_executor_releases_the_lease(tmp_path: Path) -> None:
    lock_root = tmp_path / "lease"
    holder = start_sleeping_holder(lock_root, "5")
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    while not (lock_root / "heavy-holder.json").is_file():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    holder.send_signal(signal.SIGINT)
    assert holder.wait(timeout=PROCESS_START_BUDGET_SECONDS) == 130

    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "1",
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

    retry = run_executor(
        lock_root,
        "run",
        "--lock-root",
        str(lock_root),
        "--label",
        "retry",
        "--timeout",
        "1",
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
        "1",
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
    holder = start_sleeping_holder(lock_root, "1")
    holder_path = lock_root / "heavy-holder.json"
    deadline = time.monotonic() + PROCESS_START_BUDGET_SECONDS
    try:
        while not holder_path.is_file():
            assert time.monotonic() < deadline
            time.sleep(0.01)
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
        if holder.poll() is None:
            holder.terminate()
        holder.wait(timeout=PROCESS_START_BUDGET_SECONDS)


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
