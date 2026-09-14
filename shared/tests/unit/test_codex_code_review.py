"""Contract tests for the minimal non-interactive Codex code-review runner."""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import codex_code_review as review_runner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "codex_code_review.py"
BUDGET_SCRIPT = PROJECT_ROOT / "scripts" / "codex_orchestration_budget.py"
GIT_BINARY = shutil.which("git")
CANONICAL_CHECKLIST_NAMES = tuple(sorted(review_runner.REQUIRED_CHECKLIST_SECTIONS))

pytestmark = pytest.mark.harness


def _run(
    *arguments: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed Python interpreter and project-local script.
        [sys.executable, str(SCRIPT), *arguments],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(repository: Path, *arguments: str) -> str:
    assert GIT_BINARY is not None
    result = subprocess.run(  # noqa: S603 -- fixed git executable and test-owned repository.
        [GIT_BINARY, "-C", str(repository), *arguments],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _review_message(*, candidate: str = "candidate-test-1") -> str:
    return json.dumps(
        {
            "candidate": candidate,
            "verdict": "REQUEST_CHANGES",
            "summary": "ok",
            "checklist_sections": [
                {"name": name, "status": "pass", "evidence": "checked"}
                for name in CANONICAL_CHECKLIST_NAMES
            ],
            "findings": [],
            "prior_findings": [],
        }
    )


VALID_HOTFIX = """---
document_profile: agent-primary
canonicality: canonical
owner: workflow
workflow: hotfix-v1
item: NAD-367
---

# Hotfix

## Outcome
Return the input value.

## Authorization
Owner approved this bounded fix.

## Scope
Correct identity() behavior.

## Origin
Existing implementation.

## Risks
Wrong contract selection would hide expected behavior.

## Validation
Run the focused review harness.
"""


def _candidate_repository(
    tmp_path: Path, *, item_id: str = "NAD-TEST", hotfix: bool = False
) -> tuple[Path, str]:
    repository = tmp_path / "candidate"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Review Test")
    _git(repository, "config", "user.email", "review@example.test")
    _write(repository / "AGENTS.md", "Review the approved change.\n")
    _write(repository / "docs/ARCHI.md", "# Architecture\n")
    _write(repository / "docs/coding-standards.md", "# Standards\n")
    _write(
        repository / ".claude/skills/nadili-process/review-checklist.md",
        "# Checklist\n- [ ] Correctness\n",
    )
    if hotfix:
        _write(
            repository / f"docs/work/{item_id}/hotfix.md", VALID_HOTFIX.replace("NAD-367", item_id)
        )
    else:
        _write(repository / "docs/spec.md", "# Spec\nReturn the input value.\n")
        _write(repository / "docs/plan.md", "# Plan\nImplement identity().\n")
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return value\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return 0\n")
    _git(repository, "add", "example.py")
    return repository, base


def _budget_state(tmp_path: Path, *limits: str) -> Path:
    state = tmp_path / "orchestration-budget.json"
    result = subprocess.run(  # noqa: S603 -- fixed project-local budget script.
        [sys.executable, str(BUDGET_SCRIPT), "init", "--state", str(state), *limits],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return state


def _fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

record = pathlib.Path(os.environ["FAKE_CODEX_RECORD"])
prompt = sys.stdin.read()
record.write_text(json.dumps({"argv": sys.argv[1:], "prompt": prompt}))
if os.environ.get("FAKE_MUTATE_CANDIDATE") == "1":
    candidate_directory = pathlib.Path(sys.argv[sys.argv.index("--cd") + 1])
    (candidate_directory / "example.py").write_text("changed during review")
finding = []
if os.environ.get("FAKE_CODEX_BLOCKER") == "1":
    finding = [{
        "id": "F-1",
        "origin": "initial_miss",
        "priority": "P1",
        "location": "example.py:2",
        "expected": "Return the input value.",
        "actual": "Returns zero.",
        "impact": "All non-zero inputs are wrong.",
        "action": "Return value.",
    }]
message = json.dumps({
    "candidate": os.environ["FAKE_CODEX_CANDIDATE"],
    "verdict": os.environ.get("FAKE_CODEX_VERDICT", "REQUEST_CHANGES"),
    "summary": "Independent review complete.",
    "checklist_sections": [{
        "name": name,
        "status": "fail" if finding and name == "Functional Requirements" else "pass",
        "evidence": "Inspected the applicable implementation.",
    } for name in (
        "Functional Requirements",
        "Code Quality",
        "Architectural Compliance",
        "Tenancy & Public/Private Boundary",
        "Contract & Data Integrity",
        "Jobs, Pipeline & AI Safety",
        "Concurrency & Delivery Hygiene",
        "Error Handling",
        "Security",
        "Performance & Cost",
    )],
    "findings": finding,
    "prior_findings": json.loads(os.environ.get("FAKE_PRIOR_FINDINGS", "[]")),
})
if os.environ.get("FAKE_CODEX_WRITE_FINAL", "1") == "1":
    final_path = sys.argv[sys.argv.index("--output-last-message") + 1]
    with open(final_path, "w", encoding="utf-8") as final:
        final.write(message)
print(message)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _fake_transport_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-transport-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

mode = os.environ.get("FAKE_CODEX_MODE", "complete")
if mode == "broken-pipe":
    os.close(0)
    time.sleep(0.2)
    raise SystemExit(3)
if mode == "spawn-descendant":
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4
    }}), flush=True)
    descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pathlib.Path(os.environ["FAKE_DESCENDANT_PID"]).write_text(
        str(descendant.pid), encoding="utf-8"
    )
    def stop(_signum, _frame):
        raise SystemExit(2)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        time.sleep(30)
    finally:
        descendant.terminate()
        descendant.wait(timeout=5)
if mode == "timeout":
    time.sleep(5)
if mode == "nonzero":
    print("diagnostic on stdout")
    print("secret-from-stderr", file=sys.stderr)
    raise SystemExit(7)
if mode == "complete":
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4
    }}), flush=True)
elif mode == "partial":
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "output_tokens": 4
    }}), flush=True)
elif mode == "overflow-before-final":
    print("x" * 70000, flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 25, "cached_input_tokens": 20, "output_tokens": 6
    }}), flush=True)
if mode == "final-overflow":
    message = "x" * 70000
else:
    checklist = [{
        "name": name,
        "status": "pass",
        "evidence": "Inspected the applicable implementation.",
    } for name in (
        "Functional Requirements",
        "Code Quality",
        "Architectural Compliance",
        "Tenancy & Public/Private Boundary",
        "Contract & Data Integrity",
        "Jobs, Pipeline & AI Safety",
        "Concurrency & Delivery Hygiene",
        "Error Handling",
        "Security",
        "Performance & Cost",
    )]
    message = json.dumps({
        "candidate": os.environ["FAKE_CODEX_CANDIDATE"],
        "verdict": "REQUEST_CHANGES",
        "summary": os.environ.get("FAKE_CODEX_SUMMARY", "Independent review complete."),
        "checklist_sections": checklist,
        "findings": [],
            "prior_findings": [],
    })
path = sys.argv[sys.argv.index("--output-last-message") + 1]
time.sleep(0.05)
with open(path, "w", encoding="utf-8") as final:
    final.write(message)
print(message, flush=True)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _arguments(
    *,
    repository: Path,
    base: str,
    gate_summary: Path,
    state: Path | None,
    codex_binary: Path,
    reservation_id: str = "code-review-round-1",
    attempt_id: str | None = None,
    retry_of: str | None = None,
    previous_review: Path | None = None,
    correction_diff: Path | None = None,
    item_id: str = "NAD-TEST",
    hotfix: bool = False,
) -> tuple[str, ...]:
    arguments = [
        "--worktree",
        str(repository),
        "--item-id",
        item_id,
        "--candidate",
        "candidate-test-1",
        "--diff-base",
        base,
        "--gate-summary-file",
        str(gate_summary),
        "--reservation-id",
        reservation_id,
        "--attempt-id",
        attempt_id or f"{reservation_id}-attempt",
        "--codex-bin",
        str(codex_binary),
    ]
    if hotfix:
        arguments.extend(("--hotfix", f"docs/work/{item_id}/hotfix.md"))
    else:
        arguments.extend(("--spec", "docs/spec.md", "--plan", "docs/plan.md"))
    if state is not None:
        arguments[arguments.index("--reservation-id") : arguments.index("--reservation-id")] = [
            "--budget-state",
            str(state),
        ]
    if retry_of is not None:
        arguments[arguments.index("--codex-bin") : arguments.index("--codex-bin")] = [
            "--retry-of",
            retry_of,
        ]
    if previous_review is not None:
        arguments[arguments.index("--reservation-id") : arguments.index("--reservation-id")] = [
            "--previous-review-file",
            str(previous_review),
        ]
    if correction_diff is not None:
        arguments[arguments.index("--reservation-id") : arguments.index("--reservation-id")] = [
            "--correction-diff-file",
            str(correction_diff),
        ]
    return tuple(arguments)


def test_runner_uses_minimal_read_only_codex_exec_and_records_review_budget(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    record = tmp_path / "codex-call.json"
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(record),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        "FAKE_CODEX_BLOCKER": "1",
    }

    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "REQUEST_CHANGES"
    call = json.loads(record.read_text(encoding="utf-8"))
    assert "--ephemeral" in call["argv"]
    assert "--ignore-user-config" in call["argv"]
    assert "--strict-config" in call["argv"]
    assert call["argv"][call["argv"].index("--sandbox") + 1] == "read-only"
    assert call["argv"][call["argv"].index("--model") + 1] == "gpt-6-sol"
    assert 'model_reasoning_effort="xhigh"' in call["argv"]
    assert "skills.max_context_tokens=1" in call["argv"]
    assert "features.plugins=false" in call["argv"]
    assert "mcp_servers={}" in call["argv"]
    assert "Do not read or execute any SKILL.md file" in call["prompt"]
    assert "Focused checks passed." in call["prompt"]
    assert "initial exhaustive review" in call["prompt"]
    assert "Do not stop after" in call["prompt"]
    assert "the first finding" in call["prompt"]
    budget = json.loads(state.read_text(encoding="utf-8"))
    reservation = budget["reservations"]["code-review-round-1"]
    assert reservation["kind"] == "review"
    assert reservation["agent_id"] == "codex-exec-code-reviewer"
    assert budget["usage"]["review_rounds_by_scope"] == {"code": 1}
    attempt = budget["attempts"]["code-review-round-1-attempt"]
    assert attempt["category"] == "model"
    assert attempt["effort"] == "xhigh"


def test_runner_reviews_hotfix_contract_without_inventing_an_approved_plan(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path, item_id="NAD-367", hotfix=True)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    record = tmp_path / "codex-call.json"
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(record),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
    }

    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            item_id="NAD-367",
            hotfix=True,
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    prompt = json.loads(record.read_text(encoding="utf-8"))["prompt"]
    assert "hotfix contract (authorized scope and expected behavior)" in prompt
    assert "docs/work/NAD-367/hotfix.md" in prompt
    assert "approved plan:" not in prompt


@pytest.mark.parametrize("failure", ["missing", "wrong-item", "ambiguous", "ambiguous-planned"])
def test_runner_rejects_invalid_or_ambiguous_hotfix_input(tmp_path: Path, failure: str) -> None:
    repository, base = _candidate_repository(tmp_path, item_id="NAD-367", hotfix=True)
    hotfix_path = repository / "docs/work/NAD-367/hotfix.md"
    expected: str
    if failure == "missing":
        hotfix_path.unlink()
        expected = "hotfix must be the canonical path"
    elif failure == "wrong-item":
        hotfix_path.write_text(
            VALID_HOTFIX.replace("item: NAD-367", "item: NAD-368"), encoding="utf-8"
        )
        expected = "hotfix item must be 'NAD-367'"
    elif failure == "ambiguous":
        _write(repository / "docs/work/NAD-367/plan.md", "# Ambiguous plan\n")
        expected = "cannot coexist with the canonical plan.md"
    else:
        _write(repository / "docs/work/NAD-367/plan.md", "# Ambiguous plan\n")
        _write(repository / "docs/spec.md", "# Spec\nExpected behavior.\n")
        expected = "cannot coexist with the canonical hotfix.md"
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")

    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            item_id="NAD-367",
            hotfix=failure != "ambiguous-planned",
        ),
        cwd=PROJECT_ROOT,
    )

    assert result.returncode == 2
    assert expected in result.stderr


def test_correction_review_automatically_loads_previous_result_and_builds_diff(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    record = tmp_path / "codex-call.json"
    binary = _fake_codex(tmp_path)
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(record),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
    }
    first = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=binary,
        ),
        cwd=PROJECT_ROOT,
        env={**env, "FAKE_CODEX_BLOCKER": "1"},
    )
    assert first.returncode == 0, first.stderr
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return value\n")
    _git(repository, "add", "example.py")
    second = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=binary,
            reservation_id="code-review-round-2",
        ),
        cwd=PROJECT_ROOT,
        env={
            **env,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_PRIOR_FINDINGS": json.dumps(
                [{"id": "F-1", "status": "fixed", "evidence": "example.py:2 returns value"}]
            ),
        },
    )
    assert second.returncode == 0, second.stderr
    call = json.loads(record.read_text())
    assert 'model_reasoning_effort="medium"' in call["argv"]
    assert "focused correction review" in call["prompt"]
    assert "return value" in call["prompt"] and "return 0" in call["prompt"]
    assert "do not repeat entries from its historical `prior_findings`" in call["prompt"]
    assert json.loads(second.stdout)["review_context"]["mode"] == "correction"
    assert len(list(state.with_suffix(".reviews").glob("*.json"))) == 3
    assert json.loads(state.read_text())["usage"]["review_rounds_by_scope"] == {"code": 2}


def test_rebase_only_review_refresh_is_recorded_without_spending_a_round(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path, "--max-review-rounds-per-scope", "1")
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    record = tmp_path / "codex-call.json"
    binary = _fake_codex(tmp_path)
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(record),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        "FAKE_CODEX_VERDICT": "APPROVED",
    }
    first = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=binary,
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )
    assert first.returncode == 0, first.stderr

    _git(repository, "restore", "--source=HEAD", "--staged", "--worktree", "example.py")
    _write(repository / "unrelated.txt", "upstream-only\n")
    _git(repository, "add", "unrelated.txt")
    _git(repository, "commit", "-qm", "unrelated upstream change")
    new_base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return 0\n")
    _git(repository, "add", "example.py")

    second = _run(
        *_arguments(
            repository=repository,
            base=new_base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=binary,
            reservation_id="code-review-rebase-refresh",
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout)
    assert result["review_context"]["mode"] == "rebase_refresh"
    call = json.loads(record.read_text(encoding="utf-8"))
    assert "rebase-only evidence refresh" in call["prompt"]
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["usage"]["review_rounds_by_scope"] == {"code": 1}
    assert contents["reservations"]["code-review-rebase-refresh"]["kind"] == ("review-refresh")


@pytest.mark.parametrize(
    ("invalidate", "expected"),
    (
        ("candidate", "candidate changed after reservation"),
        ("approval", "approval changed after reservation"),
    ),
)
def test_rebase_refresh_revalidates_evidence_at_attempt_start(
    tmp_path: Path, invalidate: str, expected: str
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path, "--max-review-rounds-per-scope", "1")
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(tmp_path / "codex-call.json"),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        "FAKE_CODEX_VERDICT": "APPROVED",
    }
    first = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )
    assert first.returncode == 0, first.stderr

    _git(repository, "restore", "--source=HEAD", "--staged", "--worktree", "example.py")
    _write(repository / "unrelated.txt", "upstream-only\n")
    _git(repository, "add", "unrelated.txt")
    _git(repository, "commit", "-qm", "unrelated upstream change")
    new_base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return 0\n")
    _git(repository, "add", "example.py")

    reserved = subprocess.run(  # noqa: S603 -- fixed project-local budget script.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "reserve-review-refresh",
            "--state",
            str(state),
            "--item-id",
            "NAD-TEST",
            "--id",
            "stale-refresh",
            "--review-worktree",
            str(repository),
            "--current-base",
            new_base,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reserved.returncode == 0, reserved.stderr

    if invalidate == "candidate":
        _write(repository / "example.py", "def identity(value: int) -> int:\n    return value\n")
        _git(repository, "add", "example.py")
    else:
        latest = state.with_suffix(".reviews") / "latest.json"
        verdict = json.loads(latest.read_text(encoding="utf-8"))
        verdict["verdict"] = "REQUEST_CHANGES"
        latest.write_text(json.dumps(verdict), encoding="utf-8")

    started = subprocess.run(  # noqa: S603 -- fixed project-local budget script.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "stale-refresh",
            "--attempt-id",
            "stale-refresh-attempt",
            "--stage",
            "code-review",
            "--model",
            "gpt-6-sol",
            "--effort",
            "medium",
            "--category",
            "model",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert started.returncode == 2
    assert expected in started.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert "stale-refresh-attempt" not in contents["attempts"]
    assert contents["usage"]["review_rounds_by_scope"] == {"code": 1}


def test_legacy_review_requires_explicit_snapshot_before_reservation(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.")
    previous = tmp_path / "previous.json"
    previous.write_text(_review_message())
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            previous_review=previous,
        ),
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 2
    assert "previous-tree/--previous-base" in result.stderr
    assert json.loads(state.read_text())["reservations"] == {}


def test_initial_review_requires_every_canonical_checklist_section() -> None:
    payload = json.loads(_review_message())
    payload["checklist_sections"] = [
        section for section in payload["checklist_sections"] if section["name"] != "Security"
    ]

    with pytest.raises(review_runner.ReviewRunnerError, match="initial review omitted.*Security"):
        review_runner._parse_result(json.dumps(payload), "candidate-test-1", is_initial=True)


def test_exhaustive_review_requires_every_canonical_checklist_section() -> None:
    payload = json.loads(_review_message())
    payload["checklist_sections"] = [
        section for section in payload["checklist_sections"] if section["name"] != "Security"
    ]

    with pytest.raises(review_runner.ReviewRunnerError, match="initial review omitted.*Security"):
        review_runner._parse_result(
            json.dumps(payload),
            "candidate-test-1",
            is_initial=False,
            requires_full_checklist=True,
            has_previous=True,
        )


def test_correction_result_cannot_silently_drop_a_prior_blocker() -> None:
    payload = json.loads(_review_message())
    payload["verdict"] = "APPROVED"
    payload["prior_findings"] = []
    with pytest.raises(review_runner.ReviewRunnerError, match="prior blocker"):
        review_runner._parse_result(
            json.dumps(payload), "candidate-test-1", is_initial=False, prior_ids={"F-1"}
        )


def test_packet_excludes_unrelated_upstream_bodies_after_rebase(tmp_path: Path) -> None:
    from scripts.codex_review_packet import build_packet

    repository, base = _candidate_repository(tmp_path)
    _git(repository, "add", "example.py")
    old_tree = _git(repository, "write-tree")
    _git(repository, "restore", "--source=HEAD", "--staged", "--worktree", "example.py")
    _write(repository / "unrelated.txt", "upstream-only\n" * 20000)
    _git(repository, "add", "unrelated.txt")
    _git(repository, "commit", "-qm", "upstream")
    new_base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "example.py", "def identity(value: int) -> int:\n    return value + 1\n")
    _git(repository, "add", "example.py")
    packet = build_packet(
        lambda *args: _git(repository, *args),
        base=new_base,
        previous={"review_context": {"tree": old_tree, "diff_base": base}},
        exhaustive_reason=None,
    )
    assert packet.mode == "correction"
    assert packet.correction is not None
    assert "return 0" in packet.correction and "return value + 1" in packet.correction
    assert "upstream-only" not in packet.correction + packet.base_changes
    assert "unrelated.txt" in packet.base_changes


@pytest.mark.parametrize("status", ["fixed", "rejected", "open"])
def test_prior_blocker_closure_is_enforced(status: str) -> None:
    payload = json.loads(_review_message())
    payload["verdict"] = "APPROVED"
    payload["prior_findings"] = [
        {"id": "F-1", "status": status, "evidence": "example.py:2 checked"}
    ]
    if status == "open":
        with pytest.raises(review_runner.ReviewRunnerError, match="open prior blocker"):
            review_runner._parse_result(
                json.dumps(payload), "candidate-test-1", is_initial=False, prior_ids={"F-1"}
            )
    else:
        result = review_runner._parse_result(
            json.dumps(payload), "candidate-test-1", is_initial=False, prior_ids={"F-1"}
        )
        assert result["verdict"] == "APPROVED"


def test_packet_retains_overlapping_upstream_changes_and_requires_reason_for_large_fix(
    tmp_path: Path,
) -> None:
    from scripts.codex_review_packet import PacketError, build_packet

    repository, base = _candidate_repository(tmp_path)
    old_tree = _git(repository, "write-tree")
    previous: dict[str, object] = {"review_context": {"tree": old_tree, "diff_base": base}}
    _git(repository, "restore", "--source=HEAD", "--staged", "--worktree", "example.py")
    _write(
        repository / "example.py",
        "# upstream policy\ndef identity(value: int) -> int:\n    return value\n",
    )
    _git(repository, "add", "example.py")
    _git(repository, "commit", "-qm", "upstream policy")
    new_base = _git(repository, "rev-parse", "HEAD")
    packet = build_packet(
        lambda *args: _git(repository, *args),
        base=new_base,
        previous=previous,
        exhaustive_reason=None,
    )
    assert "upstream policy" in packet.base_changes
    _write(repository / "example.py", "large correction\n" * 15000)
    _git(repository, "add", "example.py")
    with pytest.raises(PacketError, match="exhaustive-reason"):
        build_packet(
            lambda *args: _git(repository, *args),
            base=new_base,
            previous=previous,
            exhaustive_reason=None,
        )
    packet = build_packet(
        lambda *args: _git(repository, *args),
        base=new_base,
        previous=previous,
        exhaustive_reason="shared-policy-redesign",
    )
    assert packet.mode == "exhaustive" and packet.reason == "shared-policy-redesign"


def test_candidate_mutation_discards_verdict_and_preserves_failure_evidence(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate = tmp_path / "gate.txt"
    gate.write_text("checks passed")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate,
            state=state,
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(tmp_path / "call.json"),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
            "FAKE_MUTATE_CANDIDATE": "1",
        },
    )
    assert result.returncode == 2
    assert not (state.with_suffix(".reviews") / "latest.json").exists()
    attempt = next(iter(json.loads(state.read_text())["attempts"].values()))
    assert attempt["status"] == "failed" and attempt["result_code"] == "candidate_changed"


def test_legacy_review_import_generates_patch_and_assigns_stable_ids(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate = tmp_path / "gate.txt"
    gate.write_text("checks passed")
    previous = tmp_path / "previous.json"
    payload = json.loads(_review_message())
    payload.pop("prior_findings")
    payload["findings"] = [
        {
            "priority": "P1",
            "location": "example.py:2",
            "expected": "identity",
            "actual": "zero",
            "impact": "wrong output",
            "action": "return value",
        }
    ]
    previous.write_text(json.dumps(payload))
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            previous_review=previous,
        ),
        "--previous-tree",
        _git(repository, "write-tree"),
        "--previous-base",
        base,
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(tmp_path / "call.json"),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
            "FAKE_PRIOR_FINDINGS": json.dumps(
                [
                    {
                        "id": "legacy-1",
                        "status": "rejected",
                        "evidence": "Spec deliberately requires zero for these inputs",
                    }
                ]
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["review_context"]["mode"] == "correction"


def test_review_store_refuses_concurrent_reviews_for_same_item(tmp_path: Path) -> None:
    from scripts.codex_review_packet import PacketError, review_store

    with (
        review_store(tmp_path / "item.json"),
        pytest.raises(PacketError, match="another review"),
        review_store(tmp_path / "item.json"),
    ):
        pytest.fail("overlapping item review acquired lock")


def test_review_metrics_counts_later_defects_without_double_counting_latest(tmp_path: Path) -> None:
    from scripts.codex_review_packet import review_metrics, review_store, save_record

    with review_store(tmp_path / "NAD-TEST.json") as records:
        for attempt, mode, findings in (
            ("one", "initial", [{"priority": "P1", "origin": "initial_miss"}]),
            (
                "two",
                "correction",
                [
                    {"priority": "P1", "origin": "initial_miss"},
                    {"priority": "P2", "origin": "fix_regression"},
                ],
            ),
        ):
            save_record(
                records,
                attempt,
                {"review_context": {"mode": mode, "elapsed_ms": 1000}, "findings": findings},
            )
    rows = review_metrics(tmp_path, 10)
    assert rows == [
        {
            "item": "NAD-TEST",
            "completed_verdicts": 2,
            "review_elapsed_ms": 2000,
            "later_blocker_origins": {
                "initial_miss": 1,
                "fix_regression": 1,
                "unresolved": 0,
                "base_change": 0,
            },
        }
    ]


def test_runner_does_not_launch_when_start_replays_running_attempt(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    review_runner._budget_operation(
        state,
        "reserve",
        "--id",
        "restart-review",
        "--kind",
        "review",
        "--agent-id",
        "codex-exec-code-reviewer",
        "--fork-turns",
        "none",
        "--scope",
        "code",
    )
    started = review_runner._budget_operation(
        state,
        "start",
        "--reservation-id",
        "restart-review",
        "--attempt-id",
        "restart-attempt",
        "--started-at",
        "2030-01-01T00:00:00Z",
        "--stage",
        "code-review",
        "--model",
        "gpt-6-sol",
        "--effort",
        "xhigh",
        "--category",
        "model",
    )
    assert started["created"] is True
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    record = tmp_path / "codex-call.json"
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            reservation_id="restart-review",
            attempt_id="restart-attempt",
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(record),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert result.returncode == 2
    assert "was not newly created" in result.stderr
    assert not record.exists()
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["attempts"]["restart-attempt"]["status"] == "running"


def test_runner_default_uses_canonical_state_and_imports_committed_v1_source(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    legacy_path = repository / "docs/work/NAD-TEST/orchestration-budget.json"
    legacy_path.parent.mkdir(parents=True)
    legacy = json.loads(
        (PROJECT_ROOT / "tests/fixtures/codex_orchestration/legacy-review-import-v1.json").read_text(
            encoding="utf-8"
        )
    )
    legacy["limits"]["max_review_rounds_per_scope"] = 3
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    _git(repository, "add", str(legacy_path.relative_to(repository)))
    _git(repository, "commit", "-qm", "legacy budget source")
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=None,
            codex_binary=_fake_transport_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "complete",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert result.returncode == 0, result.stderr
    canonical = Path(_git(repository, "rev-parse", "--git-common-dir"))
    if not canonical.is_absolute():
        canonical = (repository / canonical).resolve()
    state = canonical / "nadili-orchestration/NAD-TEST.json"
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["imports"][0]["path"] == "docs/work/NAD-TEST/orchestration-budget.json"
    assert contents["reservations"]["code-review-round-1"]["kind"] == "review"
    assert not (repository / "docs/work/NAD-TEST/orchestration-budget.json.lock").exists()


def test_runner_rejects_budget_override_inside_candidate_repository(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=repository / "override-budget.json",
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(tmp_path / "codex-call.json"),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert result.returncode == 2
    assert "outside the candidate" in result.stderr
    assert not (tmp_path / "codex-call.json").exists()


def test_runner_rejects_relative_budget_override_before_reservation_or_launch(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=Path("docs/work/NAD-TEST/orchestration-budget.json"),
            codex_binary=_fake_transport_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "complete",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert result.returncode == 2
    assert "--budget-state must be an absolute path" in result.stderr
    assert json.loads(state.read_text(encoding="utf-8"))["reservations"] == {}
    assert not (tmp_path / "codex-call.json").exists()


def test_runner_passes_coordinator_known_attempt_and_retry_parent_to_budget(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path, "--max-review-rounds-per-scope", "3")
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")

    def budget_call(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- fixed project-local budget script.
            [sys.executable, str(BUDGET_SCRIPT), *args],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    for number in (1, 2):
        reservation = f"parent-{number}"
        assert (
            budget_call(
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation,
                "--kind",
                "review",
                "--agent-id",
                "sol",
                "--fork-turns",
                "none",
                "--scope",
                "code",
            ).returncode
            == 0
        )
        assert (
            budget_call(
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation,
                "--attempt-id",
                f"parent-attempt-{number}",
                "--stage",
                "code-review",
                "--model",
                "gpt-6-sol",
            ).returncode
            == 0
        )
        assert (
            budget_call(
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                f"parent-attempt-{number}",
                "--status",
                "failed",
                "--result-code",
                "nonzero",
            ).returncode
            == 0
        )
    report = json.loads(budget_call("report", "--state", str(state)).stdout)
    trigger = next(item for item in report["triggers"] if item["kind"] == "repeated_failure")
    assert (
        budget_call(
            "recover",
            "--state",
            str(state),
            "--trigger-id",
            trigger["trigger_id"],
            "--next-attempt-id",
            "known-retry-attempt",
            "--targeted-check-id",
            "ruff",
            "--targeted-check-result",
            "passed",
            "--next-action",
            "retry-review",
        ).returncode
        == 0
    )
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
            reservation_id="retry-reservation",
            attempt_id="known-retry-attempt",
            retry_of="parent-attempt-2",
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(tmp_path / "retry-call.json"),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert result.returncode == 0, result.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["attempts"]["known-retry-attempt"]["status"] == "succeeded"
    assert contents["recoveries"]["known-retry-attempt"]["consumed"] is True


def test_runner_rejects_an_approved_result_with_blocking_evidence(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    env = {
        **os.environ,
        "FAKE_CODEX_RECORD": str(tmp_path / "codex-call.json"),
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        "FAKE_CODEX_BLOCKER": "1",
        "FAKE_CODEX_VERDICT": "APPROVED",
    }

    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode == 2
    assert "APPROVED contradicts blocking findings" in result.stderr


def test_runner_fails_before_reservation_when_required_review_context_is_missing(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    (repository / "docs/ARCHI.md").unlink()
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    fake_codex = _fake_codex(tmp_path)

    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=fake_codex,
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_RECORD": str(tmp_path / "codex-call.json"),
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )

    assert result.returncode == 2
    assert "docs/ARCHI.md does not exist" in result.stderr
    budget = json.loads(state.read_text(encoding="utf-8"))
    assert budget["reservations"] == {}
    assert not (tmp_path / "codex-call.json").exists()


@pytest.mark.parametrize(
    ("mode", "expected_coverage"),
    [("complete", "measured"), ("partial", "measured"), ("missing", "unavailable")],
)
def test_runner_persists_complete_partial_and_missing_usage_per_field(
    tmp_path: Path, mode: str, expected_coverage: str
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / f"gate-{mode}.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    env = {
        **os.environ,
        "FAKE_CODEX_MODE": mode,
        "FAKE_CODEX_CANDIDATE": "candidate-test-1",
    }
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_transport_codex(tmp_path),
            reservation_id=f"review-{mode}",
        )
        + (("--timeout-seconds", "1") if mode == "timeout" else ()),
        cwd=PROJECT_ROOT,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    attempt = next(
        value
        for value in contents["attempts"].values()
        if value["reservation_id"] == f"review-{mode}"
    )
    assert attempt["status"] == "succeeded"
    assert attempt["tokens"]["input_tokens"]["coverage"] == expected_coverage
    assert attempt["tokens"]["cached_input_tokens"]["coverage"] == (
        "measured" if mode == "complete" else "unavailable"
    )
    assert attempt["usage_source"] == "codex_event_stream"
    assert attempt["usage_channels"] == ["stdout_jsonl"]
    assert all("secret-from-stderr" not in json.dumps(value) for value in (contents, result.stdout))


@pytest.mark.parametrize("mode", ("nonzero", "timeout", "final-overflow"))
def test_runner_terminal_failures_are_recorded_and_never_return_success(
    tmp_path: Path, mode: str
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_transport_codex(tmp_path),
            reservation_id=f"review-{mode}",
        )
        + (("--timeout-seconds", "1") if mode == "timeout" else ()),
        cwd=PROJECT_ROOT,
        env={**os.environ, "FAKE_CODEX_MODE": mode, "FAKE_CODEX_CANDIDATE": "candidate-test-1"},
    )
    assert result.returncode == 2
    contents = json.loads(state.read_text(encoding="utf-8"))
    attempts = list(contents["attempts"].values())
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"
    expected_codes = {
        "nonzero": "process_exit_7",
        "timeout": "timeout",
        "final-overflow": "final_message_overflow",
    }
    assert attempts[0]["result_code"] == expected_codes[mode]
    assert "x" * 100 not in json.dumps(contents)


def test_runner_retries_no_verdict_on_the_same_review_round(tmp_path: Path) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path, "--max-review-rounds-per-scope", "1")
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    codex_binary = _fake_transport_codex(tmp_path)
    first = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=codex_binary,
            reservation_id="round-one",
            attempt_id="attempt-one",
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "nonzero",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert first.returncode == 2

    retry = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=codex_binary,
            reservation_id="round-one",
            attempt_id="attempt-two",
            retry_of="attempt-one",
        ),
        "--retry-diagnosis",
        "process-exit-investigated",
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "complete",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
        },
    )
    assert retry.returncode == 0, retry.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["usage"]["review_rounds_by_scope"] == {"code": 1}
    assert contents["attempts"]["attempt-one"]["status"] == "failed"
    assert contents["attempts"]["attempt-two"]["status"] == "succeeded"
    assert contents["attempts"]["attempt-two"]["retry_diagnosis"] == ("process-exit-investigated")


def test_runner_terminalizes_attempt_when_child_never_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    monkeypatch.setattr(review_runner, "_build_prompt", lambda **_kwargs: "x" * 256_000)
    nonreader = tmp_path / "nonreader-codex"
    nonreader.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    nonreader.chmod(0o755)
    with pytest.raises(review_runner.ReviewRunnerError, match="timed out"):
        review_runner._run(
            review_runner._parse_args(
                list(
                    _arguments(
                        repository=repository,
                        base=base,
                        gate_summary=gate_summary,
                        state=state,
                        codex_binary=nonreader,
                        attempt_id="stdin-timeout-attempt",
                    )
                )
                + ["--timeout-seconds", "1"]
            )
        )
    attempt = json.loads(state.read_text(encoding="utf-8"))["attempts"]["stdin-timeout-attempt"]
    assert attempt["status"] == "failed"
    assert attempt["result_code"] == "timeout"


def test_broken_pipe_after_launch_is_a_process_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenPipeInput:
        def write(self, _prompt: str) -> None:
            raise BrokenPipeError

        def close(self) -> None:
            pass

    class FakeProcess:
        stdin = BrokenPipeInput()
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        returncode = 1

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

        def poll(self) -> int:
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(review_runner, "_terminate_process", lambda _process: None)
    transport = tmp_path / "transport"
    transport.mkdir()
    capture = review_runner._run_codex(["codex"], "prompt", 1, transport)
    assert capture["usage_integrity"] == "process_io_error"


@pytest.mark.parametrize("termination_signal", (signal.SIGTERM, signal.SIGHUP))
def test_signal_finalizes_attempt_and_kills_descendant_process_group(
    tmp_path: Path, termination_signal: signal.Signals
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    descendant_pid = tmp_path / "descendant.pid"
    arguments = _arguments(
        repository=repository,
        base=base,
        gate_summary=gate_summary,
        state=state,
        codex_binary=_fake_transport_codex(tmp_path),
        reservation_id="signal-review",
        attempt_id="signal-attempt",
    )
    process = subprocess.Popen(  # noqa: S603 -- fixed project-local runner under test.
        [sys.executable, str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "spawn-descendant",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
            "FAKE_DESCENDANT_PID": str(descendant_pid),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while not descendant_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert descendant_pid.exists()
    process.send_signal(termination_signal)
    process.wait(timeout=10)
    assert process.returncode == 2
    attempt = json.loads(state.read_text(encoding="utf-8"))["attempts"]["signal-attempt"]
    assert attempt["status"] == "interrupted"
    assert attempt["tokens"]["input_tokens"] == {"value": 10, "coverage": "measured"}
    descendant = int(descendant_pid.read_text(encoding="utf-8"))
    for _ in range(50):
        try:
            os.kill(descendant, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("descendant survived scoped process-group termination")


def test_popen_failure_creates_terminal_launch_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    codex = _fake_transport_codex(tmp_path)

    original_popen = subprocess.Popen

    def fail_popen(*args: object, **kwargs: object) -> object:
        command = args[0]
        if isinstance(command, (list, tuple)) and command and command[0] == str(codex):
            raise OSError("simulated launch failure")
        return cast(object, original_popen(*cast(Any, args), **cast(Any, kwargs)))

    monkeypatch.setattr(subprocess, "Popen", fail_popen)
    with pytest.raises(review_runner.ReviewRunnerError, match="cannot start codex"):
        review_runner._run(
            review_runner._parse_args(
                list(
                    _arguments(
                        repository=repository,
                        base=base,
                        gate_summary=gate_summary,
                        state=state,
                        codex_binary=codex,
                    )
                )
            )
        )
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert len(contents["reservations"]) == 1
    attempt = next(iter(contents["attempts"].values()))
    assert attempt["status"] == "failed"
    assert attempt["result_code"] == "launch_failed"


def test_stdout_result_cannot_replace_missing_authoritative_final_message(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
            "FAKE_CODEX_RECORD": str(tmp_path / "stdout-only.json"),
            "FAKE_CODEX_WRITE_FINAL": "0",
        },
    )
    assert result.returncode == 2
    contents = json.loads(state.read_text(encoding="utf-8"))
    attempt = next(iter(contents["attempts"].values()))
    assert attempt["status"] == "failed"


def test_integrity_failure_finishes_with_unavailable_usage_without_token_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    calls: list[tuple[str, tuple[str, ...]]] = []
    original_budget_operation = review_runner._budget_operation

    def record_budget_operation(
        budget_state: Path, operation: str, *arguments: str
    ) -> dict[str, object]:
        calls.append((operation, arguments))
        return original_budget_operation(budget_state, operation, *arguments)

    monkeypatch.setattr(review_runner, "_budget_operation", record_budget_operation)
    monkeypatch.setattr(
        review_runner,
        "_run_codex",
        lambda *args, **kwargs: {
            "final_message": _review_message(),
            "stdout_result": None,
            "usage": {
                "input_tokens": None,
                "cached_input_tokens": None,
                "uncached_input_tokens": None,
                "output_tokens": None,
            },
            "usage_coverage": {
                "input_tokens": "unavailable",
                "cached_input_tokens": "unavailable",
                "uncached_input_tokens": "unavailable",
                "output_tokens": "unavailable",
            },
            "usage_integrity": "malformed_usage",
            "final_overflow": False,
            "process_returncode": 0,
            "timed_out": False,
        },
    )
    result = review_runner._run(
        review_runner._parse_args(
            list(
                _arguments(
                    repository=repository,
                    base=base,
                    gate_summary=gate_summary,
                    state=state,
                    codex_binary=_fake_codex(tmp_path),
                )
            )
        )
    )
    assert result["candidate"] == "candidate-test-1"
    finish = next(arguments for operation, arguments in calls if operation == "finish")
    assert not any(
        flag
        in {
            "--input-tokens",
            "--cached-input-tokens",
            "--uncached-input-tokens",
            "--output-tokens",
        }
        for flag in finish
    )
    attempt = next(iter(json.loads(state.read_text(encoding="utf-8"))["attempts"].values()))
    assert all(token["coverage"] == "unavailable" for token in attempt["tokens"].values())


def test_keyboard_interrupt_finishes_started_attempt_and_cleans_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")

    def interrupt(*args: object, **kwargs: object) -> review_runner.TransportCapture:
        raise KeyboardInterrupt

    monkeypatch.setattr(review_runner, "_run_codex", interrupt)
    with pytest.raises(KeyboardInterrupt):
        review_runner._run(
            review_runner._parse_args(
                list(
                    _arguments(
                        repository=repository,
                        base=base,
                        gate_summary=gate_summary,
                        state=state,
                        codex_binary=_fake_codex(tmp_path),
                    )
                )
            )
        )
    attempt = next(iter(json.loads(state.read_text(encoding="utf-8"))["attempts"].values()))
    assert attempt["status"] == "interrupted"
    assert attempt["result_code"] == "interrupted"


def test_codex_keyboard_interrupt_terminates_and_reaps_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _transport_fixture(tmp_path)
    original_popen = subprocess.Popen
    original_terminate = review_runner._terminate_process
    process_holder: list[subprocess.Popen[str]] = []
    calls = 0

    def wrapped_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = cast(
            "subprocess.Popen[str]", original_popen(*cast(Any, args), **cast(Any, kwargs))
        )
        original_wait = process.wait

        def interrupt_once(timeout: float | None = None) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return original_wait(timeout=timeout)

        monkeypatch.setattr(process, "wait", interrupt_once)
        process_holder.append(process)
        return process

    terminated = False

    def record_terminate(process: subprocess.Popen[str]) -> None:
        nonlocal terminated
        terminated = True
        original_terminate(process)

    monkeypatch.setattr(subprocess, "Popen", wrapped_popen)
    monkeypatch.setattr(review_runner, "_terminate_process", record_terminate)
    with pytest.raises(KeyboardInterrupt):
        review_runner._run_codex(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            "bounded prompt",
            5,
            transport,
        )
    assert terminated is True
    assert process_holder and process_holder[0].poll() is not None


def test_launch_then_crash_can_be_reconciled_with_elapsed_and_unavailable_tokens(
    tmp_path: Path,
) -> None:
    state = _budget_state(tmp_path)
    reserve = subprocess.run(  # noqa: S603 -- fixed project-local budget command.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "reserve",
            "--state",
            str(state),
            "--id",
            "crashed-review",
            "--kind",
            "review",
            "--agent-id",
            "codex-exec-code-reviewer",
            "--fork-turns",
            "none",
            "--scope",
            "code",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reserve.returncode == 0, reserve.stderr
    started = subprocess.run(  # noqa: S603 -- fixed project-local budget command.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "crashed-review",
            "--attempt-id",
            "crashed-attempt",
            "--started-at",
            "2030-01-01T00:00:00Z",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert started.returncode == 0, started.stderr
    reconciled = subprocess.run(  # noqa: S603 -- fixed project-local budget command.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "reconcile",
            "--state",
            str(state),
            "--attempt-id",
            "crashed-attempt",
            "--ended-at",
            "2030-01-01T00:00:02Z",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reconciled.returncode == 0, reconciled.stderr
    attempt = next(iter(json.loads(state.read_text(encoding="utf-8"))["attempts"].values()))
    assert attempt["status"] == "interrupted"
    assert attempt["elapsed_ms"] == 2000
    assert all(token["coverage"] == "unavailable" for token in attempt["tokens"].values())
    revision = json.loads(state.read_text(encoding="utf-8"))["revision"]
    repeated = subprocess.run(  # noqa: S603 -- fixed project-local budget command.
        [
            sys.executable,
            str(BUDGET_SCRIPT),
            "reconcile",
            "--state",
            str(state),
            "--attempt-id",
            "crashed-attempt",
            "--ended-at",
            "2030-01-01T00:00:02Z",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(state.read_text(encoding="utf-8"))["revision"] == revision


def test_telemetry_overflow_before_valid_final_preserves_review_and_hides_secret(
    tmp_path: Path,
) -> None:
    repository, base = _candidate_repository(tmp_path)
    state = _budget_state(tmp_path)
    gate_summary = tmp_path / "gate.txt"
    gate_summary.write_text("Focused checks passed.\n", encoding="utf-8")
    secret = "SECRET-CANARY-NEVER-LEDGER"  # noqa: S105 -- deliberate non-secret canary.
    result = _run(
        *_arguments(
            repository=repository,
            base=base,
            gate_summary=gate_summary,
            state=state,
            codex_binary=_fake_transport_codex(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_CODEX_MODE": "overflow-before-final",
            "FAKE_CODEX_CANDIDATE": "candidate-test-1",
            "FAKE_CODEX_SUMMARY": secret,
        },
    )
    assert result.returncode == 0, result.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    report = json.loads(
        subprocess.run(  # noqa: S603 -- fixed project-local budget command.
            [sys.executable, str(BUDGET_SCRIPT), "report", "--state", str(state)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )
    attempt = next(iter(contents["attempts"].values()))
    assert attempt["status"] == "succeeded"
    assert attempt["usage_integrity"] == "telemetry_overflow"
    assert attempt["tokens"]["input_tokens"] == {"coverage": "measured", "value": 25}
    assert attempt["tokens"]["cached_input_tokens"] == {"coverage": "measured", "value": 20}
    assert attempt["tokens"]["uncached_input_tokens"] == {"coverage": "measured", "value": 5}
    assert attempt["tokens"]["output_tokens"] == {"coverage": "measured", "value": 6}
    assert secret not in json.dumps(contents)
    assert secret not in json.dumps(report)


def _capture_jsonl(text: str) -> review_runner.TransportCapture:
    capture: review_runner.TransportCapture = {
        "final_message": None,
        "stdout_result": None,
        "usage": {
            "input_tokens": None,
            "cached_input_tokens": None,
            "uncached_input_tokens": None,
            "output_tokens": None,
        },
        "usage_coverage": {},
        "usage_integrity": None,
        "usage_source": "unavailable",
        "usage_channels": [],
        "final_overflow": False,
        "process_returncode": None,
        "timed_out": False,
    }
    review_runner._collect_jsonl(io.StringIO(text), capture)
    return capture


def test_jsonl_usage_keeps_final_monotonic_snapshot_and_ignores_duplicates() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":15,"cached_input_tokens":4,"output_tokens":7}}\n'
    )
    assert capture["usage"] == {
        "input_tokens": 15,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 11,
        "output_tokens": 7,
    }
    assert capture["usage_integrity"] is None


def test_jsonl_staggered_snapshots_are_recanonicalized() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":5}}\n'
    )
    assert capture["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "uncached_input_tokens": 8,
        "output_tokens": 5,
    }
    assert capture["usage_integrity"] is None


def test_jsonl_cumulative_update_merges_raw_fields_before_deriving_uncached() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":15,"output_tokens":7}}\n'
    )
    assert capture["usage"] == {
        "input_tokens": 15,
        "cached_input_tokens": 2,
        "uncached_input_tokens": 13,
        "output_tokens": 7,
    }
    assert capture["usage_coverage"] == {
        "input_tokens": "measured",
        "cached_input_tokens": "measured",
        "uncached_input_tokens": "measured",
        "output_tokens": "measured",
    }
    assert capture["usage_integrity"] is None


def test_jsonl_usage_invalidates_only_cached_and_dependents_when_relation_conflicts() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"cached_input_tokens":11,"output_tokens":4}}\n'
    )
    assert capture["usage_integrity"] == "malformed_usage"
    assert capture["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "output_tokens": 4,
    }
    assert capture["usage_coverage"]["input_tokens"] == "measured"
    assert capture["usage_coverage"]["output_tokens"] == "measured"


def test_jsonl_usage_requires_input_and_cached_for_standalone_uncached() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"uncached_input_tokens":8,"output_tokens":3}}\n'
    )
    assert capture["usage_integrity"] == "malformed_usage"
    assert capture["usage"]["uncached_input_tokens"] is None
    assert capture["usage"]["output_tokens"] == 3


def test_jsonl_usage_invalidates_conflicting_uncached_without_losing_sources() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2}}\n'
        '{"type":"turn.completed","usage":{"uncached_input_tokens":9}}\n'
    )
    assert capture["usage_integrity"] == "malformed_usage"
    assert capture["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "uncached_input_tokens": None,
        "output_tokens": None,
    }


def test_jsonl_usage_invalidates_regressive_uncached_only() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"uncached_input_tokens":8,"output_tokens":3}}\n'
        '{"type":"turn.completed","usage":{"uncached_input_tokens":7,"output_tokens":4}}\n'
    )
    assert capture["usage_integrity"] == "regressive_usage"
    assert capture["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "uncached_input_tokens": None,
        "output_tokens": 4,
    }


def test_nonempty_malformed_jsonl_invalidates_a_prior_measured_snapshot() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}\n'
        "not-json\n"
    )
    assert capture["usage_integrity"] == "malformed_usage"
    assert all(value is None for value in capture["usage"].values())


def test_jsonl_usage_preserves_partial_coverage_and_missing_channel() -> None:
    partial = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}\n'
    )
    missing = _capture_jsonl('{"type":"turn.started"}\n')
    assert partial["usage_coverage"]["input_tokens"] == "measured"
    assert partial["usage_coverage"]["cached_input_tokens"] == "unavailable"
    assert missing["usage_integrity"] is None
    assert all(value is None for value in missing["usage"].values())


@pytest.mark.parametrize(
    ("text", "integrity"),
    [
        ('{"type":"turn.completed","usage":{"input_tokens":"secret"}}\n', "malformed_usage"),
        (
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":4}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":9,"cached_input_tokens":2,"output_tokens":4}}\n',
            "regressive_usage",
        ),
    ],
)
def test_jsonl_usage_marks_malformed_or_regressive_snapshots_unavailable(
    text: str, integrity: str
) -> None:
    capture = _capture_jsonl(text)
    assert capture["usage_integrity"] == integrity
    if integrity == "malformed_usage":
        assert all(value is None for value in capture["usage"].values())
        assert all(value == "unavailable" for value in capture["usage_coverage"].values())
    else:
        assert capture["usage"] == {
            "input_tokens": None,
            "cached_input_tokens": 2,
            "uncached_input_tokens": None,
            "output_tokens": 4,
        }
        assert capture["usage_coverage"] == {
            "input_tokens": "unavailable",
            "cached_input_tokens": "measured",
            "uncached_input_tokens": "unavailable",
            "output_tokens": "measured",
        }


def test_jsonl_usage_preserves_unrelated_fields_when_output_regresses() -> None:
    capture = _capture_jsonl(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":5}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":4}}\n'
    )
    assert capture["usage_integrity"] == "regressive_usage"
    assert capture["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "uncached_input_tokens": 8,
        "output_tokens": None,
    }
    assert capture["usage_coverage"]["uncached_input_tokens"] == "measured"


def test_canonical_usage_derives_or_rejects_uncached_input() -> None:
    usage = review_runner._canonical_usage(
        {"input_tokens": 10, "cached_input_tokens": 2, "uncached_input_tokens": 8}
    )
    assert usage is not None
    assert usage["uncached_input_tokens"] == 8
    assert (
        review_runner._canonical_usage(
            {"input_tokens": 10, "cached_input_tokens": 2, "uncached_input_tokens": 9}
        )
        is None
    )
    assert review_runner._canonical_usage({"input_tokens": 10, "uncached_input_tokens": 8}) is None


def test_jsonl_overflow_drains_and_does_not_retain_secret_payload() -> None:
    canary = "CANARY-DO-NOT-STORE"
    capture = _capture_jsonl("x" * (review_runner.MAX_JSONL_LINE_BYTES + 1) + canary + "\n")
    assert capture["usage_integrity"] == "telemetry_overflow"
    assert canary not in str(capture)


def test_stale_scavenger_removes_only_marked_same_uid_directories(tmp_path: Path) -> None:
    stale = tmp_path / "nadili-codex-review-stale"
    stale.mkdir()
    (stale / "marker").write_text(review_runner.TRANSPORT_MARKER, encoding="utf-8")
    old = 1.0
    os.utime(stale, (old, old))
    unmarked = tmp_path / "nadili-codex-review-unmarked"
    unmarked.mkdir()
    os.utime(unmarked, (old, old))
    review_runner._scavenge_transport(tmp_path)
    assert not stale.exists()
    assert unmarked.exists()


def _transport_fixture(tmp_path: Path) -> Path:
    transport = tmp_path / "transport"
    transport.mkdir(parents=True)
    os.mkfifo(transport / "final-message", 0o600)
    return transport


def test_codex_stdin_delivery_is_bounded_when_child_never_reads(
    tmp_path: Path,
) -> None:
    transport = _transport_fixture(tmp_path)
    capture = review_runner._run_codex(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        "prompt-" + ("x" * 256_000),
        1,
        transport,
    )
    assert capture["timed_out"] is True
    assert capture["usage_integrity"] == "timeout"
    assert capture["process_returncode"] is not None


def test_codex_process_nonzero_is_not_a_successful_review(tmp_path: Path) -> None:
    transport = _transport_fixture(tmp_path)
    capture = review_runner._run_codex(
        [sys.executable, "-c", "print('{}'); raise SystemExit(7)"],
        "bounded prompt",
        5,
        transport,
    )
    assert capture["process_returncode"] == 7
    assert capture["timed_out"] is False


def test_codex_timeout_is_recorded_even_when_process_prints_a_result(tmp_path: Path) -> None:
    transport = _transport_fixture(tmp_path)
    capture = review_runner._run_codex(
        [sys.executable, "-c", "import time; print('{}'); time.sleep(5)"],
        "bounded prompt",
        1,
        transport,
    )
    assert capture["timed_out"] is True
    assert capture["process_returncode"] is not None


def test_final_message_pipe_is_authoritative_and_separately_bounded(tmp_path: Path) -> None:
    transport = _transport_fixture(tmp_path)
    message = '{"candidate":"c","verdict":"REQUEST_CHANGES","summary":"ok","checklist_sections":[],"findings":[]}'
    code = (
        "import sys; p=sys.argv[sys.argv.index('--output-last-message')+1]; "
        f"open(p,'w').write({message!r})"
    )
    capture = review_runner._run_codex(
        [sys.executable, "-c", code, "--output-last-message", str(transport / "final-message")],
        "bounded prompt",
        5,
        transport,
    )
    assert capture["final_message"] == message
    assert capture["final_overflow"] is False


def test_concurrent_final_message_collectors_keep_each_fifo_message(tmp_path: Path) -> None:
    messages = [
        '{"candidate":"one","verdict":"REQUEST_CHANGES","summary":"one","checklist_sections":[],"findings":[]}',
        '{"candidate":"two","verdict":"REQUEST_CHANGES","summary":"two","checklist_sections":[],"findings":[]}',
    ]

    def run(message: str, name: str) -> review_runner.TransportCapture:
        transport = _transport_fixture(tmp_path / name)
        code = (
            "import sys,time; p=sys.argv[sys.argv.index('--output-last-message')+1]; "
            "time.sleep(.02); open(p,'w').write(sys.argv[-1])"
        )
        return review_runner._run_codex(
            [
                sys.executable,
                "-c",
                code,
                "--output-last-message",
                str(transport / "final-message"),
                message,
            ],
            "bounded prompt",
            5,
            transport,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        captures = list(pool.map(run, messages, ("one", "two")))
    assert [capture["final_message"] for capture in captures] == messages
    assert all(not capture["final_overflow"] for capture in captures)


def test_final_message_overflow_is_bounded_and_never_retained(tmp_path: Path) -> None:
    transport = _transport_fixture(tmp_path)
    capture = _capture_jsonl("")
    payload = b"x" * (review_runner.MAX_FINAL_MESSAGE_BYTES + 1)

    def write_pipe() -> None:
        with open(transport / "final-message", "wb") as pipe:
            pipe.write(payload)

    writer = threading.Thread(target=write_pipe)
    writer.start()

    class FinishedProcess:
        def poll(self) -> int:
            return 0

    review_runner._collect_final(
        transport / "final-message", capture, cast(subprocess.Popen[str], FinishedProcess())
    )
    writer.join(timeout=2)
    assert capture["final_overflow"] is True
    assert capture["final_message"] is None
