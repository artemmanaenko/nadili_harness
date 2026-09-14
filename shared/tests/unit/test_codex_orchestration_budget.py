"""Contract tests for the Codex orchestration action budget ledger."""

from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts import codex_orchestration_budget as budget

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts/codex_orchestration_budget.py"


def _run_budget(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed project-local CLI under test.
        [sys.executable, str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _init_state(tmp_path: Path, *limits: str) -> Path:
    state = tmp_path / "orchestration-budget.json"
    result = _run_budget(tmp_path, "init", "--state", str(state), *limits)
    assert result.returncode == 0, result.stderr
    return state


def test_init_refuses_to_reset_an_existing_ledger(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    reset = _run_budget(tmp_path, "init", "--state", str(state))

    assert reset.returncode == 2
    assert "budget state already exists" in reset.stderr


def test_default_gate_limits_allow_six_fast_and_two_integration_checks(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    limits = json.loads(state.read_text(encoding="utf-8"))["limits"]
    assert limits["max_fast_gates"] == 6
    assert limits["max_integration_checks"] == 2


def test_review_allows_two_diagnosed_no_verdict_retries_on_one_round(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-review-rounds-per-scope", "1")
    reserved = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "review-round",
        "--kind",
        "review",
        "--agent-id",
        "sol",
        "--fork-turns",
        "none",
        "--scope",
        "code",
    )
    assert reserved.returncode == 0, reserved.stderr

    for number in range(1, 4):
        attempt_id = f"review-attempt-{number}"
        start_args = [
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review-round",
            "--attempt-id",
            attempt_id,
            "--stage",
            "code-review",
            "--model",
            "sol",
        ]
        if number > 1:
            start_args.extend(("--retry-of", f"review-attempt-{number - 1}"))
            missing_diagnosis = _run_budget(tmp_path, *start_args)
            assert missing_diagnosis.returncode == 2
            start_args.extend(("--retry-diagnosis", "transport-timeout-checked"))
        started = _run_budget(tmp_path, *start_args)
        assert started.returncode == 0, started.stderr
        finish_args = [
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            attempt_id,
            "--status",
            "failed",
            "--result-code",
            f"transport_timeout_{number}",
        ]
        if number == 1:
            finish_args.extend(("--input-tokens", "100", "--cached-input-tokens", "20"))
            finish_args.extend(("--output-tokens", "30"))
        finished = _run_budget(tmp_path, *finish_args)
        assert finished.returncode == 0, finished.stderr

    exhausted = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review-round",
        "--attempt-id",
        "review-attempt-4",
        "--stage",
        "code-review",
        "--model",
        "sol",
        "--retry-of",
        "review-attempt-3",
        "--retry-diagnosis",
        "timeout-checked",
    )
    assert exhausted.returncode == 2
    assert "review retry requires" in exhausted.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["usage"]["review_rounds_by_scope"] == {"code": 1}
    assert len(contents["attempts"]) == 3
    assert contents["attempts"]["review-attempt-1"]["tokens"]["input_tokens"] == {
        "coverage": "measured",
        "value": 100,
    }
    assert contents["attempts"]["review-attempt-2"]["retry_diagnosis"] == (
        "transport-timeout-checked"
    )


def test_review_verdict_cannot_be_retried_without_a_new_round(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-review-rounds-per-scope", "1")
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "review-round",
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
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review-round",
            "--attempt-id",
            "review-attempt-1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "review-attempt-1",
            "--status",
            "succeeded",
            "--result-code",
            "review_succeeded",
        ).returncode
        == 0
    )

    retry = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review-round",
        "--attempt-id",
        "review-attempt-2",
        "--retry-of",
        "review-attempt-1",
        "--retry-diagnosis",
        "checking-findings",
    )
    assert retry.returncode == 2
    assert "review retry requires" in retry.stderr


def test_reserve_agent_is_idempotent_and_records_thread_target_overage(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-agent-threads", "1")
    first = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "executor-first-batch",
        "--kind",
        "agent",
        "--agent-id",
        "luna-executor",
        "--fork-turns",
        "none",
    )
    repeated = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "executor-first-batch",
        "--kind",
        "agent",
        "--agent-id",
        "luna-executor",
        "--fork-turns",
        "none",
    )
    second = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "sol-reviewer",
        "--kind",
        "agent",
        "--agent-id",
        "sol-reviewer",
        "--fork-turns",
        "none",
    )

    assert first.returncode == 0, first.stderr
    assert json.loads(repeated.stdout)["status"] == "already_reserved"
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "over_budget"
    assert json.loads(second.stdout)["over_budget"]["agent_threads"] == 1


def test_reserve_records_context_budget_alerts_without_blocking_work(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    all_history = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "bad-history",
        "--kind",
        "agent",
        "--agent-id",
        "luna",
        "--fork-turns",
        "all",
    )
    unapproved_history = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "unapproved-history",
        "--kind",
        "agent",
        "--agent-id",
        "luna",
        "--fork-turns",
        "4",
    )
    approved_history = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "approved-history",
        "--kind",
        "agent",
        "--agent-id",
        "luna",
        "--fork-turns",
        "4",
        "--history-exception",
        "Candidate diff lacks context required by the isolated brief.",
    )

    assert all_history.returncode == 0, all_history.stderr
    assert 'fork_turns="all" defeats the context budget' in json.loads(all_history.stdout)["alerts"]
    assert unapproved_history.returncode == 0, unapproved_history.stderr
    assert (
        "bounded history lacks a written --history-exception"
        in json.loads(unapproved_history.stdout)["alerts"]
    )
    assert approved_history.returncode == 0, approved_history.stderr


def test_reserve_live_eval_records_missing_evidence_and_default_target_overage(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    no_static_check = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "eval-without-contract",
        "--kind",
        "live-eval",
    )
    first = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "eval-suite",
        "--kind",
        "live-eval",
        "--units",
        "12",
        "--static-check",
        "tests/unit/test_prompt_contract.py passed",
    )
    over_cap = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "eval-extra",
        "--kind",
        "live-eval",
        "--static-check",
        "tests/unit/test_prompt_contract.py passed",
    )

    assert no_static_check.returncode == 0, no_static_check.stderr
    assert (
        "live eval has no recorded static-contract check"
        in json.loads(no_static_check.stdout)["alerts"]
    )
    assert first.returncode == 0, first.stderr
    assert over_cap.returncode == 0, over_cap.stderr
    assert "live evaluation call target exceeded" in json.loads(over_cap.stdout)["alerts"]


def test_review_fast_gate_and_integration_budgets_are_independent(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    review_commands = [
        (
            "reserve",
            "--state",
            str(state),
            "--id",
            f"code-review-{round_number}",
            "--kind",
            "review",
            "--agent-id",
            "sol-code-reviewer",
            "--fork-turns",
            "none",
            "--scope",
            "code",
        )
        for round_number in (1, 2, 3)
    ]
    first_review, second_review, third_review = [
        _run_budget(tmp_path, *arguments) for arguments in review_commands
    ]
    fast_gates = [
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            f"candidate-fast-gate-{gate_number}",
            "--kind",
            "fast-gate",
        )
        for gate_number in range(1, 8)
    ]
    integration_checks = [
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            f"candidate-integration-{number}",
            "--kind",
            "integration-check",
        )
        for number in range(1, 4)
    ]

    assert first_review.returncode == 0, first_review.stderr
    assert second_review.returncode == 0, second_review.stderr
    assert third_review.returncode == 0, third_review.stderr
    assert (
        "review round target exceeded for scope: code" in json.loads(third_review.stdout)["alerts"]
    )
    assert all(gate.returncode == 0 for gate in fast_gates)
    assert all(not json.loads(gate.stdout)["alerts"] for gate in fast_gates[:6])
    assert "full fast gate target exceeded" in json.loads(fast_gates[6].stdout)["alerts"]
    assert all(check.returncode == 0 for check in integration_checks)
    assert all(not json.loads(check.stdout)["alerts"] for check in integration_checks[:2])
    assert "integration-check target exceeded" in json.loads(integration_checks[2].stdout)["alerts"]


def test_public_reserve_rejects_manual_review_refresh(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    result = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "manual-refresh",
        "--kind",
        "review-refresh",
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert json.loads(state.read_text(encoding="utf-8"))["reservations"] == {}


def test_amend_records_explicit_owner_approval_for_a_larger_eval_budget(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    amended = _run_budget(
        tmp_path,
        "amend",
        "--state",
        str(state),
        "--owner-approval",
        "Owner approved 24 live calls after static and local ladder.",
        "--max-live-eval-calls",
        "24",
    )
    contents = json.loads(state.read_text(encoding="utf-8"))

    assert amended.returncode == 0, amended.stderr
    assert contents["limits"]["max_live_eval_calls"] == 24
    assert contents["amendments"] == [
        {
            "changes": {"max_live_eval_calls": 24},
            "owner_approval": "Owner approved 24 live calls after static and local ladder.",
        }
    ]


def test_attempt_lifecycle_reports_measured_tokens_and_reserved_without_attempt(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    reserve = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "review",
        "--kind",
        "review",
        "--agent-id",
        "sol",
        "--fork-turns",
        "none",
        "--scope",
        "code",
    )
    assert reserve.returncode == 0, reserve.stderr
    start = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review",
        "--attempt-id",
        "attempt-1",
        "--started-at",
        "2026-01-01T00:00:00Z",
    )
    assert json.loads(start.stdout)["created"] is True
    replay_start = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review",
        "--attempt-id",
        "attempt-1",
    )
    assert replay_start.returncode == 0, replay_start.stderr
    assert json.loads(replay_start.stdout)["created"] is False
    finish = _run_budget(
        tmp_path,
        "finish",
        "--state",
        str(state),
        "--attempt-id",
        "attempt-1",
        "--status",
        "succeeded",
        "--result-code",
        "ok",
        "--ended-at",
        "2026-01-01T00:00:01Z",
        "--input-tokens",
        "10",
        "--cached-input-tokens",
        "3",
        "--output-tokens",
        "4",
    )
    report = _run_budget(tmp_path, "report", "--state", str(state))
    assert start.returncode == finish.returncode == 0
    assert report.returncode == 0
    payload = json.loads(report.stdout)
    assert payload["attempt_count"] == 1
    assert payload["reserved_without_attempt"] == []
    assert payload["groups"][0]["tokens"]["uncached_input_tokens"] == 7
    revision = json.loads(state.read_text(encoding="utf-8"))["revision"]
    repeated_finish = _run_budget(
        tmp_path,
        "finish",
        "--state",
        str(state),
        "--attempt-id",
        "attempt-1",
        "--status",
        "succeeded",
        "--result-code",
        "ok",
    )
    assert repeated_finish.returncode == 0, repeated_finish.stderr
    assert json.loads(state.read_text(encoding="utf-8"))["revision"] == revision


def test_retry_rejects_a_nonterminal_parent(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    for reservation_id in ("first", "second"):
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
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
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "first",
            "--attempt-id",
            "a1",
        ).returncode
        == 0
    )
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "second",
        "--attempt-id",
        "a2",
        "--retry-of",
        "a1",
    )
    assert blocked.returncode == 2
    assert "nonterminal attempt" in blocked.stderr


def test_agent_reservation_allows_sequential_turns_but_not_concurrent_turns(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "agent",
            "--kind",
            "agent",
            "--agent-id",
            "luna",
            "--fork-turns",
            "none",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "agent",
            "--attempt-id",
            "turn-1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "turn-1",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "agent",
            "--attempt-id",
            "turn-2",
        ).returncode
        == 0
    )
    concurrent = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "agent",
        "--attempt-id",
        "turn-3",
    )
    assert concurrent.returncode == 2
    assert "running attempt" in concurrent.stderr


def test_live_eval_units_bound_distinct_attempts(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "eval",
            "--kind",
            "live-eval",
            "--units",
            "2",
            "--static-check",
            "contract",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "eval",
            "--attempt-id",
            "eval-1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "eval",
            "--attempt-id",
            "eval-2",
        ).returncode
        == 0
    )
    third = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "eval",
        "--attempt-id",
        "eval-3",
    )
    assert third.returncode == 2
    assert "cardinality" in third.stderr


def test_repeated_failure_requires_exact_one_shot_recovery(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-review-rounds-per-scope", "4")

    def failed_attempt(reservation_id: str, attempt_id: str, ended_at: str) -> None:
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
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
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation_id,
                "--attempt-id",
                attempt_id,
                "--started-at",
                "2029-12-31T23:59:59Z",
                "--stage",
                "review",
                "--model",
                "sol",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                attempt_id,
                "--status",
                "failed",
                "--result-code",
                "timeout",
                "--ended-at",
                ended_at,
            ).returncode
            == 0
        )

    failed_attempt("first", "a1", "2030-01-01T00:00:01Z")
    failed_attempt("second", "a2", "2030-01-01T00:00:02Z")
    trigger = next(
        item
        for item in json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)[
            "triggers"
        ]
        if item["kind"] == "repeated_failure"
    )
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "retry",
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
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry",
        "--attempt-id",
        "a3",
        "--retry-of",
        "a2",
        "--stage",
        "review",
        "--model",
        "sol",
    )
    assert blocked.returncode == 2
    assert (
        _run_budget(
            tmp_path,
            "recover",
            "--state",
            str(state),
            "--trigger-id",
            trigger["trigger_id"],
            "--next-attempt-id",
            "a3",
            "--targeted-check-id",
            "ruff",
            "--targeted-check-result",
            "passed",
            "--next-action",
            "retry-review",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "retry",
            "--attempt-id",
            "a3",
            "--retry-of",
            "a2",
            "--stage",
            "review",
            "--model",
            "sol",
        ).returncode
        == 0
    )
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["recoveries"]["a3"]["consumed"] is True
    # The authorized retry itself fails.  Its consumed authorization must close
    # only the old trigger, allowing this new failure streak to create a fresh one.
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "a3",
            "--status",
            "failed",
            "--result-code",
            "timeout",
            "--ended-at",
            "2030-01-01T00:00:03Z",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    fresh_trigger = next(
        item
        for item in report["triggers"]
        if item["kind"] == "repeated_failure" and item["attempt_id"] == "a3"
    )
    assert fresh_trigger["blocking"] is True
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "retry-2",
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
        _run_budget(
            tmp_path,
            "recover",
            "--state",
            str(state),
            "--trigger-id",
            fresh_trigger["trigger_id"],
            "--next-attempt-id",
            "a4",
            "--targeted-check-id",
            "ruff",
            "--targeted-check-result",
            "passed",
            "--next-action",
            "retry-review",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "retry-2",
            "--attempt-id",
            "a4",
            "--retry-of",
            "a3",
            "--stage",
            "review",
            "--model",
            "sol",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "a4",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
            "--ended-at",
            "2030-01-01T00:00:04Z",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    trigger_projection = next(
        item for item in report["triggers"] if item["trigger_id"] == trigger["trigger_id"]
    )
    assert trigger_projection["blocking"] is False
    assert trigger_projection["resolved_by"] == "a3"
    fresh_projection = next(
        item for item in report["triggers"] if item["trigger_id"] == fresh_trigger["trigger_id"]
    )
    assert fresh_projection["blocking"] is False
    assert fresh_projection["resolved_by"] == "a4"


def test_recovery_accepts_one_lineage_for_explicitly_covered_sibling_failures(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path, "--max-live-eval-calls", "5")
    for ordinal in range(1, 5):
        reservation_id = f"eval-{ordinal}"
        attempt_id = f"eval-{ordinal}-attempt"
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "live-eval",
                "--stage",
                "evaluation",
                "--model",
                "luna",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation_id,
                "--attempt-id",
                attempt_id,
            ).returncode
            == 0
        )
    for ordinal in range(1, 5):
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                f"eval-{ordinal}-attempt",
                "--status",
                "failed",
                "--result-code",
                "validation-error",
            ).returncode
            == 0
        )

    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "targeted-retry",
            "--kind",
            "live-eval",
            "--stage",
            "evaluation",
            "--model",
            "luna",
            "--retry-of",
            "eval-4-attempt",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    triggers = [item for item in report["triggers"] if item["kind"] == "repeated_failure"]
    assert len(triggers) == 3
    for trigger in triggers:
        assert (
            _run_budget(
                tmp_path,
                "recover",
                "--state",
                str(state),
                "--trigger-id",
                trigger["trigger_id"],
                "--next-attempt-id",
                "targeted-retry-attempt",
                "--targeted-check-id",
                "safe-validation-diagnostics",
                "--targeted-check-result",
                "passed",
                "--next-action",
                "targeted-retry",
            ).returncode
            == 0
        )

    started = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "targeted-retry",
        "--attempt-id",
        "targeted-retry-attempt",
    )

    assert started.returncode == 0, started.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["recoveries"]["targeted-retry-attempt"]["consumed"] is True
    assert all(not trigger["blocking"] for trigger in contents["triggers"].values())


def test_repeated_failure_uses_terminal_completion_order_and_not_insertion_order(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)

    def fail(
        reservation_id: str,
        attempt_id: str,
        stage: str,
        ended_at: str,
        retry_of: str | None = None,
    ) -> None:
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "review",
                "--agent-id",
                reservation_id,
                "--fork-turns",
                "none",
                "--scope",
                reservation_id,
            ).returncode
            == 0
        )
        start_args = [
            "start",
            "--state",
            str(state),
            "--reservation-id",
            reservation_id,
            "--attempt-id",
            attempt_id,
            "--stage",
            stage,
            "--model",
            "sol",
            "--started-at",
            "2030-01-01T00:00:00Z",
        ]
        if retry_of is not None:
            start_args.extend(("--retry-of", retry_of))
        assert (
            _run_budget(
                tmp_path,
                *start_args,
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                attempt_id,
                "--status",
                "failed",
                "--result-code",
                "timeout",
                "--ended-at",
                ended_at,
            ).returncode
            == 0
        )

    fail("a1-reservation", "a1", "review-a", "2030-01-01T00:00:01Z")
    fail("a2-reservation", "a2", "review-a", "2030-01-01T00:00:03Z")
    fail("b-reservation", "b", "review-b", "2030-01-01T00:00:02Z")
    trigger = next(
        item
        for item in json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)[
            "triggers"
        ]
        if item["kind"] == "repeated_failure"
    )
    assert (
        _run_budget(
            tmp_path,
            "recover",
            "--state",
            str(state),
            "--trigger-id",
            trigger["trigger_id"],
            "--next-attempt-id",
            "a3",
            "--targeted-check-id",
            "ordering-test",
            "--targeted-check-result",
            "passed",
            "--next-action",
            "continue",
        ).returncode
        == 0
    )
    fail("a3-reservation", "a3", "review-a", "2030-01-01T00:00:04Z", retry_of="a2")
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    triggers = [item for item in report["triggers"] if item["kind"] == "repeated_failure"]
    assert {trigger["attempt_id"] for trigger in triggers} == {"a2", "a3"}
    assert triggers[-1]["attempt_id"] == "a3"


def test_repeated_failure_does_not_skip_a_success_or_interruption_between_failures(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)

    def attempt(reservation_id: str, attempt_id: str, status: str, ended_at: str) -> None:
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "review",
                "--agent-id",
                reservation_id,
                "--fork-turns",
                "none",
                "--scope",
                reservation_id,
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation_id,
                "--attempt-id",
                attempt_id,
                "--stage",
                "review",
                "--model",
                "sol",
                "--started-at",
                "2030-01-01T00:00:00Z",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                attempt_id,
                "--status",
                status,
                "--result-code",
                status,
                "--ended-at",
                ended_at,
            ).returncode
            == 0
        )

    attempt("fail-1", "a1", "failed", "2030-01-01T00:00:01Z")
    attempt("success", "a2", "succeeded", "2030-01-01T00:00:02Z")
    attempt("fail-2", "a3", "failed", "2030-01-01T00:00:03Z")
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert not any(item["kind"] == "repeated_failure" for item in report["triggers"])


def test_report_unions_overlapping_category_intervals_and_exposes_missing_reservations(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    for reservation_id, category, attempt_id, start, end in (
        ("model", "model", "m1", "2030-01-01T00:00:00Z", "2030-01-01T00:00:10Z"),
        ("gate", "gate", "g1", "2030-01-01T00:00:05Z", "2030-01-01T00:00:15Z"),
        ("reserved", "model", "", "", ""),
    ):
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "review",
                "--agent-id",
                reservation_id,
                "--fork-turns",
                "none",
                "--scope",
                reservation_id,
                "--category",
                category,
            ).returncode
            == 0
        )
        if attempt_id:
            assert (
                _run_budget(
                    tmp_path,
                    "start",
                    "--state",
                    str(state),
                    "--reservation-id",
                    reservation_id,
                    "--attempt-id",
                    attempt_id,
                    "--started-at",
                    start,
                    "--category",
                    category,
                ).returncode
                == 0
            )
            assert (
                _run_budget(
                    tmp_path,
                    "finish",
                    "--state",
                    str(state),
                    "--attempt-id",
                    attempt_id,
                    "--status",
                    "succeeded",
                    "--result-code",
                    "ok",
                    "--ended-at",
                    end,
                ).returncode
                == 0
            )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert report["wall_time_ms"] == 15000
    assert report["activity_time_ms_by_category"] == {"gate": 10000, "model": 10000}
    assert report["overlap_time_ms"] == 5000
    assert report["reserved_without_attempt"] == ["reserved"]


@pytest.mark.parametrize(
    ("intervals", "expected_overlap"),
    [
        (
            [
                ("2030-01-01T00:00:00Z", "2030-01-01T00:00:10Z"),
                ("2030-01-01T00:00:05Z", "2030-01-01T00:00:15Z"),
            ],
            5000,
        ),
        (
            [
                ("2030-01-01T00:00:00Z", "2030-01-01T00:00:10Z"),
                ("2030-01-01T00:00:02Z", "2030-01-01T00:00:08Z"),
                ("2030-01-01T00:00:04Z", "2030-01-01T00:00:06Z"),
            ],
            6000,
        ),
    ],
)
def test_report_overlap_sweeps_individual_attempts(
    intervals: list[tuple[str, str]], expected_overlap: int
) -> None:
    state = budget._base_state()
    state["attempts"] = {
        f"a{index}": {
            "attempt_id": f"a{index}",
            "reservation_id": f"r{index}",
            "status": "succeeded",
            "started_at": start,
            "ended_at": end,
            "category": "model",
            "tokens": {},
        }
        for index, (start, end) in enumerate(intervals)
    }
    state["reservations"] = {
        f"r{index}": {"kind": "review", "units": 1, "scope": str(index)}
        for index in range(len(intervals))
    }
    report = budget._report(state)
    assert report["overlap_time_ms"] == expected_overlap


def test_accepted_offsets_are_persisted_as_utc_and_replay_equivalently(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "review",
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
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review",
            "--attempt-id",
            "offset-attempt",
            "--started-at",
            "2030-01-01T02:00:00+02:00",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "offset-attempt",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
            "--ended-at",
            "2030-01-01T04:00:00+02:00",
        ).returncode
        == 0
    )
    attempt = json.loads(state.read_text(encoding="utf-8"))["attempts"]["offset-attempt"]
    assert attempt["started_at"] == "2030-01-01T00:00:00.000Z"
    assert attempt["ended_at"] == "2030-01-01T02:00:00.000Z"
    replay = _run_budget(
        tmp_path,
        "finish",
        "--state",
        str(state),
        "--attempt-id",
        "offset-attempt",
        "--status",
        "succeeded",
        "--result-code",
        "ok",
        "--ended-at",
        "2030-01-01T04:00:00+02:00",
    )
    assert replay.returncode == 0, replay.stderr


def test_report_counts_running_attempts_as_eligible_without_elapsed_observation(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "running-gate",
            "--kind",
            "fast-gate",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "running-gate",
            "--attempt-id",
            "running-attempt",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert report["attempt_count"] == 1
    assert report["wall_time_ms"] == 0
    assert report["coverage"]["tokens"]["input_tokens"] == {
        "eligible_attempts": 1,
        "measured_attempts": 0,
        "coverage": "none",
    }


def test_report_lists_each_measured_field_once_per_group(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "agent",
            "--kind",
            "agent",
            "--agent-id",
            "worker",
            "--fork-turns",
            "none",
        ).returncode
        == 0
    )
    for number in (1, 2):
        attempt_id = f"attempt-{number}"
        assert (
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                "agent",
                "--attempt-id",
                attempt_id,
                "--stage",
                "implementation",
                "--model",
                "worker",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                attempt_id,
                "--status",
                "succeeded",
                "--result-code",
                "ok",
                "--input-tokens",
                "10",
                "--cached-input-tokens",
                "2",
                "--output-tokens",
                "4",
            ).returncode
            == 0
        )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert report["groups"][0]["measured_fields"] == [
        "cached_input_tokens",
        "input_tokens",
        "output_tokens",
        "uncached_input_tokens",
    ]


def test_v1_projection_is_exact_and_does_not_leak_v2_reservation_fields() -> None:
    state = budget._base_state()
    state["reservations"] = {
        "review": {
            "kind": "review",
            "units": 1,
            "agent_id": "sol",
            "fork_turns": "none",
            "scope": "code",
            "stage": "review",
            "model": "gpt",
            "effort": "high",
            "category": "model",
            "reason": "internal",
            "retry_of": "prior",
        }
    }
    projected = budget._v1_projection(state)
    reservations = cast(dict[str, dict[str, object]], projected["reservations"])
    assert set(reservations["review"]) == {
        "kind",
        "units",
        "agent_id",
        "fork_turns",
        "scope",
    }


def test_v1_projection_preserves_free_refresh_as_agent_accounting() -> None:
    state = budget._base_state()
    state["reservations"] = {
        "refresh": {
            "kind": "review-refresh",
            "units": 1,
            "agent_id": "sol",
            "fork_turns": "none",
            "scope": "code",
        }
    }

    projected = budget._v1_projection(state)
    reservations = cast(dict[str, dict[str, object]], projected["reservations"])
    assert reservations["refresh"] == {
        "kind": "agent",
        "units": 1,
        "agent_id": "sol",
        "fork_turns": "none",
    }


def test_state_validation_rejects_inconsistent_reservation_derived_counters() -> None:
    state = budget._base_state()
    state["reservations"] = {"eval": {"kind": "live-eval", "units": 2}}
    with pytest.raises(budget.BudgetError, match="reservation-derived"):
        budget._validate_state(state)


def test_report_group_membership_is_safe_when_stage_and_model_contain_slashes() -> None:
    state = budget._base_state()
    state["attempts"] = {
        "one": {
            "attempt_id": "one",
            "reservation_id": "r1",
            "status": "succeeded",
            "started_at": "2030-01-01T00:00:00Z",
            "ended_at": "2030-01-01T00:00:01Z",
            "stage": "a/b",
            "model": "c",
            "tokens": {},
        },
        "two": {
            "attempt_id": "two",
            "reservation_id": "r2",
            "status": "succeeded",
            "started_at": "2030-01-01T00:00:02Z",
            "ended_at": "2030-01-01T00:00:03Z",
            "stage": "a",
            "model": "b/c",
            "tokens": {},
        },
    }
    state["reservations"] = {
        "r1": {"kind": "review", "units": 1, "scope": "x"},
        "r2": {"kind": "review", "units": 1, "scope": "y"},
    }
    groups = cast(list[dict[str, object]], budget._report(state)["groups"])
    assert {(item["stage"], item["model"]) for item in groups} == {
        ("a/b", "c"),
        ("a", "b/c"),
    }


def test_single_attempt_kind_rejects_a_second_attempt_after_terminal_finish(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "gate",
            "--kind",
            "fast-gate",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "gate",
            "--attempt-id",
            "first",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "first",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
        ).returncode
        == 0
    )
    second = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "gate",
        "--attempt-id",
        "second",
    )
    assert second.returncode == 2
    assert "cardinality" in second.stderr


@pytest.mark.parametrize("fixture_name", ("legacy-budget-overruns-v1.json", "legacy-unknown-agent-history-v1.json", "legacy-review-import-v1.json"))
def test_golden_v1_fixtures_preserve_limits_reservations_and_legacy_observation(
    fixture_name: str,
) -> None:
    source = PROJECT_ROOT / "tests" / "fixtures" / "codex_orchestration" / fixture_name
    raw = json.loads(source.read_text(encoding="utf-8"))
    state = budget._legacy_to_v2(raw, source)
    assert state["schema_version"] == 2
    assert state["limits"] == raw["limits"]
    assert state["reservations"] == raw["reservations"]
    assert state["usage"] == raw["usage"]
    if fixture_name == "legacy-unknown-agent-history-v1.json":
        assert any(agent["fork_turns"] == "unknown" for agent in raw["agents"].values())
    assert any("alerts" in reservation for reservation in raw["reservations"].values())
    reservations = cast(dict[str, dict[str, object]], state["reservations"])
    assert any("alerts" in reservation for reservation in reservations.values())
    assert state["observations"] == [
        {"kind": "legacy_unresolved", "blocking": False, "source": "v1"}
    ]


def test_over_budget_reservation_creates_a_blocking_v2_trigger(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-live-eval-calls", "1")
    result = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "eval-over",
        "--kind",
        "live-eval",
        "--units",
        "2",
        "--static-check",
        "contract-pass",
    )
    assert result.returncode == 0
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["schema_version"] == 2
    assert any(
        item["kind"] == "budget_overage" and item["blocking"]
        for item in contents["triggers"].values()
    )


def test_model_review_overage_cannot_be_recovered_until_owner_amendment(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-review-rounds-per-scope", "2")
    for reservation_id in ("review-1", "review-2", "review-3"):
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
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
    trigger = next(
        item
        for item in json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)[
            "triggers"
        ]
        if item["kind"] == "budget_overage"
    )
    before = json.loads(state.read_text(encoding="utf-8"))
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review-3",
        "--attempt-id",
        "review-3-attempt",
    )
    assert blocked.returncode == 2
    assert "explicit owner-approved amendment required" in blocked.stderr
    unchanged = json.loads(state.read_text(encoding="utf-8"))
    assert unchanged["revision"] == before["revision"]
    assert "review-3-attempt" not in unchanged["attempts"]

    recovery_blocked = _run_budget(
        tmp_path,
        "recover",
        "--state",
        str(state),
        "--trigger-id",
        trigger["trigger_id"],
        "--next-attempt-id",
        "review-3-attempt",
        "--targeted-check-id",
        "ruff",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-review",
    )
    assert recovery_blocked.returncode == 2
    assert "explicit owner-approved amendment required" in recovery_blocked.stderr
    assert "review-3-attempt" not in json.loads(state.read_text(encoding="utf-8"))["recoveries"]

    amended = _run_budget(
        tmp_path,
        "amend",
        "--state",
        str(state),
        "--owner-approval",
        "Owner approved the third review round.",
        "--max-review-rounds-per-scope",
        "3",
    )
    assert amended.returncode == 0, amended.stderr
    recovered = _run_budget(
        tmp_path,
        "recover",
        "--state",
        str(state),
        "--trigger-id",
        trigger["trigger_id"],
        "--next-attempt-id",
        "review-3-attempt",
        "--targeted-check-id",
        "ruff",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-review",
    )
    assert recovered.returncode == 0, recovered.stderr
    started = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review-3",
        "--attempt-id",
        "review-3-attempt",
    )
    assert started.returncode == 0, started.stderr
    assert (
        json.loads(state.read_text(encoding="utf-8"))["recoveries"]["review-3-attempt"]["consumed"]
        is True
    )


def test_model_thread_ceiling_blocks_start_and_recovery_until_amended(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-agent-threads", "1")
    for reservation_id, agent_id in (("first", "first-agent"), ("over-limit", "second-agent")):
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "agent",
                "--agent-id",
                agent_id,
                "--fork-turns",
                "none",
            ).returncode
            == 0
        )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    trigger = next(item for item in report["triggers"] if item["kind"] == "budget_overage")
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "over-limit",
        "--attempt-id",
        "over-limit-attempt",
    )
    assert blocked.returncode == 2
    assert "explicit owner-approved amendment required" in blocked.stderr
    recovery = _run_budget(
        tmp_path,
        "recover",
        "--state",
        str(state),
        "--trigger-id",
        trigger["trigger_id"],
        "--next-attempt-id",
        "over-limit-attempt",
        "--targeted-check-id",
        "ruff",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-model",
    )
    assert recovery.returncode == 2
    assert "explicit owner-approved amendment required" in recovery.stderr


def test_live_eval_ceiling_blocks_start_and_recovery_until_amended(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-live-eval-calls", "1")
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "over-limit",
            "--kind",
            "live-eval",
            "--units",
            "2",
            "--static-check",
            "contract-pass",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    trigger = next(item for item in report["triggers"] if item["kind"] == "budget_overage")
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "over-limit",
        "--attempt-id",
        "over-limit-attempt",
    )
    assert blocked.returncode == 2
    assert "explicit owner-approved amendment required" in blocked.stderr
    recovery = _run_budget(
        tmp_path,
        "recover",
        "--state",
        str(state),
        "--trigger-id",
        trigger["trigger_id"],
        "--next-attempt-id",
        "over-limit-attempt",
        "--targeted-check-id",
        "ruff",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-model",
    )
    assert recovery.returncode == 2
    assert "explicit owner-approved amendment required" in recovery.stderr


def test_over_budget_retry_requires_exact_recovery_and_consumes_once(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-fast-gates", "1")
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "first-gate",
            "--kind",
            "fast-gate",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "first-gate",
            "--attempt-id",
            "first-gate-attempt",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "first-gate-attempt",
            "--status",
            "failed",
            "--result-code",
            "gate-failed",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "retry-gate",
            "--kind",
            "fast-gate",
            "--retry-of",
            "first-gate-attempt",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    trigger = next(item for item in report["triggers"] if item["kind"] == "budget_overage")
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        "retry-gate-attempt",
        "--retry-of",
        "first-gate-attempt",
    )
    assert blocked.returncode == 2
    assert "exact blocking trigger" in blocked.stderr
    recovered = _run_budget(
        tmp_path,
        "recover",
        "--state",
        str(state),
        "--trigger-id",
        trigger["trigger_id"],
        "--next-attempt-id",
        "retry-gate-attempt",
        "--targeted-check-id",
        "gate-recheck",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-gate",
    )
    assert recovered.returncode == 0, recovered.stderr
    started = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        "retry-gate-attempt",
        "--retry-of",
        "first-gate-attempt",
    )
    assert started.returncode == 0, started.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["recoveries"]["retry-gate-attempt"]["consumed"] is True
    different_attempt = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        "different-attempt",
        "--retry-of",
        "first-gate-attempt",
    )
    assert different_attempt.returncode == 2


def test_start_requires_and_consumes_recovery_for_all_simultaneous_triggers(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path, "--max-fast-gates", "2")
    for reservation_id, attempt_id in (
        ("first-gate", "first-attempt"),
        ("second-gate", "second-attempt"),
    ):
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation_id,
                "--kind",
                "fast-gate",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation_id,
                "--attempt-id",
                attempt_id,
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                attempt_id,
                "--status",
                "failed",
                "--result-code",
                "gate-failed",
            ).returncode
            == 0
        )
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "retry-gate",
            "--kind",
            "fast-gate",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    triggers = report["triggers"]
    repeated = next(item for item in triggers if item["kind"] == "repeated_failure")
    overage = next(item for item in triggers if item["kind"] == "budget_overage")
    attempt_id = "retry-attempt"
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        attempt_id,
        "--retry-of",
        "second-attempt",
    )
    assert blocked.returncode == 2
    assert "exact blocking trigger" in blocked.stderr
    recover_args = (
        "recover",
        "--state",
        str(state),
        "--next-attempt-id",
        attempt_id,
        "--targeted-check-id",
        "gate-recheck",
        "--targeted-check-result",
        "passed",
        "--next-action",
        "retry-gate",
    )
    first_recovery = _run_budget(
        tmp_path, *recover_args[:1], *recover_args[1:], "--trigger-id", repeated["trigger_id"]
    )
    assert first_recovery.returncode == 0, first_recovery.stderr
    still_blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        attempt_id,
        "--retry-of",
        "second-attempt",
    )
    assert still_blocked.returncode == 2
    assert "every applicable" in still_blocked.stderr
    second_recovery = _run_budget(
        tmp_path, *recover_args[:1], *recover_args[1:], "--trigger-id", overage["trigger_id"]
    )
    assert second_recovery.returncode == 0, second_recovery.stderr
    started = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry-gate",
        "--attempt-id",
        attempt_id,
        "--retry-of",
        "second-attempt",
    )
    assert started.returncode == 0, started.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    recovery = contents["recoveries"][attempt_id]
    assert set(recovery["trigger_ids"]) == {repeated["trigger_id"], overage["trigger_id"]}
    assert recovery["consumed"] is True
    assert all(
        item["blocking"] is False
        for item in contents["triggers"].values()
        if item["trigger_id"] in recovery["trigger_ids"]
    )


def test_report_marks_missing_usage_as_none_not_zero(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "review",
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
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review",
            "--attempt-id",
            "a1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "a1",
            "--status",
            "succeeded",
            "--result-code",
            "missing-usage",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert report["coverage"]["model_usage"] == "none"
    assert report["coverage"]["tokens"]["input_tokens"]["measured_attempts"] == 0


def test_over_budget_reservation_replays_exactly_with_derived_alerts(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-fast-gates", "1")
    arguments = (
        "reserve",
        "--state",
        str(state),
        "--id",
        "fast-2",
        "--kind",
        "fast-gate",
    )
    first = _run_budget(tmp_path, *arguments)
    repeated = _run_budget(tmp_path, *arguments)
    assert first.returncode == repeated.returncode == 0
    assert json.loads(repeated.stdout)["status"] == "already_reserved"


def test_finish_replay_keeps_terminal_timestamp_and_derives_uncached_input(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "review",
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
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review",
            "--attempt-id",
            "a1",
            "--started-at",
            "2030-01-01T00:00:00Z",
        ).returncode
        == 0
    )
    finish_args = (
        "finish",
        "--state",
        str(state),
        "--attempt-id",
        "a1",
        "--status",
        "succeeded",
        "--result-code",
        "ok",
        "--ended-at",
        "2030-01-01T00:00:01Z",
        "--input-tokens",
        "10",
        "--cached-input-tokens",
        "2",
    )
    assert _run_budget(tmp_path, *finish_args).returncode == 0
    before = json.loads(state.read_text(encoding="utf-8"))
    repeated = _run_budget(
        tmp_path,
        "finish",
        "--state",
        str(state),
        "--attempt-id",
        "a1",
        "--status",
        "succeeded",
        "--result-code",
        "ok",
    )
    assert repeated.returncode == 0
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after == before
    assert after["attempts"]["a1"]["tokens"]["uncached_input_tokens"]["value"] == 8
    mismatch = _run_budget(tmp_path, *finish_args, "--uncached-input-tokens", "9")
    assert mismatch.returncode == 2


def test_reconcile_interrupted_replay_compares_supplied_terminal_fields(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "agent",
            "--kind",
            "agent",
            "--agent-id",
            "luna",
            "--fork-turns",
            "none",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "agent",
            "--attempt-id",
            "a1",
            "--started-at",
            "2030-01-01T00:00:00Z",
        ).returncode
        == 0
    )
    reconcile = (
        "reconcile",
        "--state",
        str(state),
        "--attempt-id",
        "a1",
        "--result-code",
        "crashed",
        "--ended-at",
        "2030-01-01T00:00:01+00:00",
    )
    assert _run_budget(tmp_path, *reconcile).returncode == 0
    assert _run_budget(tmp_path, *reconcile).returncode == 0
    mismatch = _run_budget(tmp_path, *(reconcile[:6] + ("different",) + reconcile[7:]))
    assert mismatch.returncode == 2


def test_start_cannot_omit_retry_link_to_bypass_failure_trigger(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-review-rounds-per-scope", "3")
    for number in (1, 2):
        reservation = f"failed-{number}"
        assert (
            _run_budget(
                tmp_path,
                "reserve",
                "--state",
                str(state),
                "--id",
                reservation,
                "--kind",
                "review",
                "--agent-id",
                f"sol-{number}",
                "--fork-turns",
                "none",
                "--scope",
                "code",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                reservation,
                "--attempt-id",
                f"failed-attempt-{number}",
                "--stage",
                "review",
                "--model",
                "sol",
            ).returncode
            == 0
        )
        assert (
            _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                f"failed-attempt-{number}",
                "--status",
                "failed",
                "--result-code",
                "timeout",
            ).returncode
            == 0
        )
    assert (
        _run_budget(
            tmp_path,
            "reserve",
            "--state",
            str(state),
            "--id",
            "retry",
            "--kind",
            "review",
            "--agent-id",
            "new-sol",
            "--fork-turns",
            "none",
            "--scope",
            "code",
            "--stage",
            "review",
            "--model",
            "sol",
        ).returncode
        == 0
    )
    blocked = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "retry",
        "--attempt-id",
        "retry-attempt",
        "--stage",
        "review",
        "--model",
        "sol",
    )
    assert blocked.returncode == 2
    assert "authorization" in blocked.stderr


def test_v1_import_is_idempotent_and_rejects_a_changed_source(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "limits": {
                    "max_agent_threads": 8,
                    "max_live_eval_calls": 12,
                    "max_review_rounds_per_scope": 2,
                    "max_fast_gates": 1,
                    "max_integration_checks": 1,
                },
                "agents": {},
                "usage": {
                    "agent_ids": [],
                    "live_eval_calls": 0,
                    "review_rounds_by_scope": {},
                    "fast_gates": 0,
                    "integration_checks": 0,
                },
                "reservations": {},
                "amendments": [],
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "canonical.json"
    first = _run_budget(tmp_path, "import", "--state", str(state), "--source", str(source))
    repeated = _run_budget(tmp_path, "import", "--state", str(state), "--source", str(source))
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    conflict = _run_budget(tmp_path, "import", "--state", str(state), "--source", str(source))
    assert first.returncode == 0, first.stderr
    assert json.loads(repeated.stdout)["status"] == "already_imported"
    assert conflict.returncode == 2
    assert "conflicting legacy source" in conflict.stderr


@pytest.mark.parametrize("change_kind", ("unstaged", "staged", "deleted", "directory"))
def test_init_rejects_mutable_legacy_source_instead_of_fingerprinting_head(
    tmp_path: Path, change_kind: str
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git = shutil.which("git")
    assert git is not None
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
    ):
        subprocess.run([git, "-C", str(repository), *arguments], check=True)  # noqa: S603
    source = repository / "docs/work/NAD-322/orchestration-budget.json"
    fixture = PROJECT_ROOT / "tests/fixtures/codex_orchestration/legacy-review-import-v1.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(fixture.read_bytes())
    subprocess.run(  # noqa: S603 -- fixed git executable and test-owned repository.
        [git, "-C", str(repository), "add", str(source.relative_to(repository))], check=True
    )
    subprocess.run([git, "-C", str(repository), "commit", "-qm", "legacy"], check=True)  # noqa: S603
    if change_kind == "deleted":
        source.unlink()
    elif change_kind == "directory":
        source.unlink()
        source.mkdir()
    else:
        source.write_bytes(source.read_bytes() + b"\n")
    if change_kind == "staged":
        subprocess.run(  # noqa: S603 -- fixed git executable and test-owned repository.
            [git, "-C", str(repository), "add", str(source.relative_to(repository))], check=True
        )
    result = _run_budget(tmp_path, "init", "--worktree", str(repository), "--item-id", "NAD-322")
    assert result.returncode == 2
    assert "committed" in result.stderr


def test_init_rejects_limit_override_when_importing_committed_legacy_source(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git = shutil.which("git")
    assert git is not None
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
    ):
        subprocess.run([git, "-C", str(repository), *arguments], check=True)  # noqa: S603
    source = repository / "docs/work/NAD-322/orchestration-budget.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        (PROJECT_ROOT / "tests/fixtures/codex_orchestration/legacy-review-import-v1.json").read_bytes()
    )
    subprocess.run(  # noqa: S603 -- fixed git executable and test-owned repository.
        [git, "-C", str(repository), "add", str(source.relative_to(repository))], check=True
    )
    subprocess.run([git, "-C", str(repository), "commit", "-qm", "legacy"], check=True)  # noqa: S603
    result = _run_budget(
        tmp_path,
        "init",
        "--worktree",
        str(repository),
        "--item-id",
        "NAD-322",
        "--max-live-eval-calls",
        "99",
    )
    assert result.returncode == 2
    assert "overrides are forbidden" in result.stderr


def test_canonical_import_rejects_arbitrary_source_without_explicit_state(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git = shutil.which("git")
    assert git is not None
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
    ):
        subprocess.run([git, "-C", str(repository), *arguments], check=True)  # noqa: S603
    source = repository / "docs/work/NAD-322/orchestration-budget.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        (PROJECT_ROOT / "tests/fixtures/codex_orchestration/legacy-review-import-v1.json").read_bytes()
    )
    arbitrary = tmp_path / "arbitrary.json"
    arbitrary.write_bytes(source.read_bytes())
    subprocess.run(  # noqa: S603 -- fixed git executable and test-owned repository.
        [git, "-C", str(repository), "add", str(source.relative_to(repository))], check=True
    )
    subprocess.run([git, "-C", str(repository), "commit", "-qm", "legacy"], check=True)  # noqa: S603
    result = _run_budget(
        tmp_path,
        "import",
        "--worktree",
        str(repository),
        "--item-id",
        "NAD-322",
        "--source",
        str(arbitrary),
    )
    assert result.returncode == 2
    assert "canonical import source" in result.stderr


def test_omitted_category_is_reported_as_unknown_and_arbitrary_category_is_rejected(
    tmp_path: Path,
) -> None:
    state = _init_state(tmp_path)
    reservation = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "review",
        "--kind",
        "review",
        "--agent-id",
        "sol",
        "--fork-turns",
        "none",
        "--scope",
        "code",
    )
    assert reservation.returncode == 0
    started = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review",
        "--attempt-id",
        "a1",
        "--category",
        "billing",
    )
    assert started.returncode == 2
    assert "closed orchestration category" in started.stderr
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review",
            "--attempt-id",
            "a1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "a1",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
        ).returncode
        == 0
    )
    report = json.loads(_run_budget(tmp_path, "report", "--state", str(state)).stdout)
    assert "unknown" in report["activity_time_ms_by_category"]


def test_state_write_uses_restricted_modes_and_clears_journal(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    budget._write_state(state, budget._base_state())
    assert state.stat().st_mode & 0o777 == 0o600
    assert state.parent.stat().st_mode & 0o777 == 0o700
    assert not state.with_suffix(".json.journal").exists()
    assert not state.with_suffix(".json.lock").exists()


def test_journal_recovery_clears_a_failure_after_journal_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    calls = 0
    original = budget._fsync_dir

    def fail_after_journal(directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected fsync failure")
        original(directory)

    monkeypatch.setattr(budget, "_fsync_dir", fail_after_journal)
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._write_state(state, budget._base_state())
    assert state.with_suffix(".json.journal").exists()
    lock = budget._with_lock(state)
    budget._release_lock(lock)
    assert not state.with_suffix(".json.journal").exists()


def test_git_worktrees_share_canonical_state_and_preserve_concurrent_mutations(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    linked = tmp_path / "linked"
    repository.mkdir()
    git_binary = shutil.which("git")
    assert git_binary is not None
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "init", "-q"], check=True
    )
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "config", "user.name", "Test"], check=True
    )
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "config", "user.email", "test@example.test"], check=True
    )
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "add", "tracked.txt"], check=True
    )
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "commit", "-qm", "base"], check=True
    )
    subprocess.run(  # noqa: S603, S607 -- fixed git test executable.
        [git_binary, "-C", str(repository), "worktree", "add", "-q", "-b", "linked", str(linked)],
        check=True,
    )

    first_path = budget.canonical_state_path(repository, "NAD-322")
    second_path = budget.canonical_state_path(linked, "NAD-322")
    assert first_path == second_path
    assert (
        _run_budget(
            tmp_path, "init", "--worktree", str(repository), "--item-id", "NAD-322"
        ).returncode
        == 0
    )

    def reserve(reservation_id: str, agent_id: str) -> subprocess.CompletedProcess[str]:
        return _run_budget(
            tmp_path,
            "reserve",
            "--worktree",
            str(linked if reservation_id == "r2" else repository),
            "--item-id",
            "NAD-322",
            "--id",
            reservation_id,
            "--kind",
            "agent",
            "--agent-id",
            agent_id,
            "--fork-turns",
            "none",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, ("r1", "r2"), ("a1", "a2")))
    assert all(result.returncode == 0 for result in reservations), [r.stderr for r in reservations]

    def start(attempt_id: str, reservation_id: str) -> subprocess.CompletedProcess[str]:
        return _run_budget(
            tmp_path,
            "start",
            "--worktree",
            str(linked if reservation_id == "r2" else repository),
            "--item-id",
            "NAD-322",
            "--reservation-id",
            reservation_id,
            "--attempt-id",
            attempt_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(start, ("a1", "a2"), ("r1", "r2")))
    assert all(result.returncode == 0 for result in attempts), [r.stderr for r in attempts]
    state = json.loads(first_path.read_text(encoding="utf-8"))
    assert set(state["reservations"]) == {"r1", "r2"}
    assert set(state["attempts"]) == {"a1", "a2"}


def test_state_root_rejects_traversal_and_symlinks_and_enforces_modes(tmp_path: Path) -> None:
    root = tmp_path / "state-root"
    traversal = _run_budget(
        tmp_path,
        "init",
        "--state-root",
        str(root),
        "--item-id",
        "../escape",
    )
    assert traversal.returncode == 2
    assert "unsafe path" in traversal.stderr

    real_root = tmp_path / "real-root"
    real_root.mkdir()
    real_root.chmod(0o755)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    root_link = _run_budget(
        tmp_path,
        "init",
        "--state-root",
        str(linked_root),
        "--item-id",
        "NAD-322",
    )
    assert root_link.returncode == 2
    assert "symlink" in root_link.stderr

    state = tmp_path / "real-state.json"
    state_link = tmp_path / "state-link.json"
    state_link.symlink_to(state)
    file_link = _run_budget(tmp_path, "init", "--state", str(state_link))
    assert file_link.returncode == 2
    assert "symlink" in file_link.stderr

    assert (
        _run_budget(
            tmp_path, "init", "--state-root", str(real_root), "--item-id", "NAD-322"
        ).returncode
        == 0
    )
    assert real_root.stat().st_mode & 0o777 == 0o755
    assert (real_root / "NAD-322.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("location", ("root", "reservation", "attempt", "token", "retry"))
def test_schema_rejects_unknown_sensitive_fields(tmp_path: Path, location: str) -> None:
    state = _init_state(tmp_path)
    contents = json.loads(state.read_text(encoding="utf-8"))
    if location == "root":
        contents["raw_prompt"] = "secret-canary"
    elif location == "reservation":
        contents["reservations"]["r"] = {
            "kind": "review",
            "units": 1,
            "secret": "secret-canary",
        }
    elif location == "attempt":
        contents["reservations"]["r"] = {"kind": "review", "units": 1}
        contents["attempts"]["a"] = {
            "attempt_id": "a",
            "reservation_id": "r",
            "status": "running",
            "started_at": "2030-01-01T00:00:00Z",
            "stderr": "secret-canary",
        }
    elif location == "token":
        contents["reservations"]["r"] = {"kind": "review", "units": 1}
        contents["attempts"]["a"] = {
            "attempt_id": "a",
            "reservation_id": "r",
            "status": "running",
            "started_at": "2030-01-01T00:00:00Z",
            "tokens": {"input_tokens": {"coverage": "unavailable", "value": 1}},
        }
    else:
        contents["reservations"]["r"] = {"kind": "review", "units": 1}
        contents["attempts"]["a"] = {
            "attempt_id": "a",
            "reservation_id": "r",
            "status": "running",
            "started_at": "2030-01-01T00:00:00Z",
            "retry_of": "missing-parent",
        }
    state.write_text(json.dumps(contents), encoding="utf-8")
    report = _run_budget(tmp_path, "report", "--state", str(state))
    assert report.returncode == 2
    assert "secret-canary" not in report.stderr


def test_report_serialization_is_bounded(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    contents = json.loads(state.read_text(encoding="utf-8"))
    contents["reservations"] = {
        f"r-{index}": {"kind": "live-eval", "units": 1, "static_check": "x" * 2000}
        for index in range(budget.MAX_ATTEMPTS)
    }
    contents["triggers"] = {
        f"t-{index}": {
            "trigger_id": f"t-{index}",
            "kind": "budget_overage",
            "reservation_id": f"r-{index}",
            "blocking": True,
            "created_at": "2030-01-01T00:00:00Z",
            "signature": ["x" * 2000, None, None, None],
        }
        for index in range(budget.MAX_ATTEMPTS)
    }
    contents["usage"]["live_eval_calls"] = budget.MAX_ATTEMPTS
    state.write_text(json.dumps(contents), encoding="utf-8")
    report = _run_budget(tmp_path, "report", "--state", str(state))
    assert report.returncode == 2
    assert "bound" in report.stderr


@pytest.mark.parametrize(
    ("boundary", "failure_call"),
    [
        ("journal_fsync", 1),
        ("state_fsync", 2),
    ],
)
def test_journal_fsync_boundaries_recover_prior_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    failure_call: int,
) -> None:
    state = tmp_path / f"{boundary}.json"
    calls = 0
    original_fsync = os.fsync

    def fail_at(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError(boundary)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_at)
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._write_state(state, budget._base_state())
    monkeypatch.setattr(os, "fsync", original_fsync)
    if state.with_suffix(".json.journal").exists():
        lock = budget._with_lock(state)
        budget._release_lock(lock)
        assert not state.with_suffix(".json.journal").exists()
    else:
        assert not state.exists()


@pytest.mark.parametrize("replace_call", (1, 2))
def test_journal_replace_boundaries_recover_prior_or_applied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_call: int
) -> None:
    state = tmp_path / f"replace-{replace_call}.json"
    calls = 0
    original_replace = os.replace

    def fail_at(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == replace_call:
            raise OSError("replace")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_at)
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._write_state(state, budget._base_state())
    monkeypatch.setattr(os, "replace", original_replace)
    if state.with_suffix(".json.journal").exists():
        lock = budget._with_lock(state)
        budget._release_lock(lock)
        assert not state.with_suffix(".json.journal").exists()
    else:
        assert not state.exists()


@pytest.mark.parametrize("dir_call", (1, 2, 3))
def test_journal_directory_boundaries_recover_or_leave_applied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dir_call: int
) -> None:
    state = tmp_path / f"dir-{dir_call}.json"
    calls = 0
    original_dir_fsync = budget._fsync_dir

    def fail_at(directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == dir_call:
            raise OSError("directory fsync")
        original_dir_fsync(directory)

    monkeypatch.setattr(budget, "_fsync_dir", fail_at)
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._write_state(state, budget._base_state())
    monkeypatch.setattr(budget, "_fsync_dir", original_dir_fsync)
    if state.with_suffix(".json.journal").exists():
        lock = budget._with_lock(state)
        budget._release_lock(lock)
    assert not state.with_suffix(".json.journal").exists()


def test_journal_unlink_failure_recovers_applied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "unlink.json"
    original_unlink = Path.unlink

    def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == state.with_suffix(".json.journal"):
            raise OSError("journal unlink")
        original_unlink(path)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._write_state(state, budget._base_state())
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert state.exists()
    lock = budget._with_lock(state)
    budget._release_lock(lock)
    assert not state.with_suffix(".json.journal").exists()


def test_ambiguous_journal_hash_blocks_recovery(tmp_path: Path) -> None:
    state = tmp_path / "ambiguous.json"
    budget._write_state(state, budget._base_state())
    state.with_suffix(".json.journal").write_text(
        json.dumps({"version": 1, "previous_hash": "bad", "intended_hash": "also-bad"}),
        encoding="utf-8",
    )
    with pytest.raises(budget.BudgetError, match="ambiguous"):
        budget._with_lock(state)


def _rollback_fixture(tmp_path: Path) -> tuple[Path, Path, str, dict[str, object]]:
    state = _init_state(tmp_path)
    reserve = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "review",
        "--kind",
        "review",
        "--agent-id",
        "sol",
        "--fork-turns",
        "none",
        "--scope",
        "code",
    )
    assert reserve.returncode == 0, reserve.stderr
    assert (
        _run_budget(
            tmp_path,
            "start",
            "--state",
            str(state),
            "--reservation-id",
            "review",
            "--attempt-id",
            "attempt-1",
        ).returncode
        == 0
    )
    assert (
        _run_budget(
            tmp_path,
            "finish",
            "--state",
            str(state),
            "--attempt-id",
            "attempt-1",
            "--status",
            "succeeded",
            "--result-code",
            "ok",
        ).returncode
        == 0
    )
    export = tmp_path / "rollback.json"
    result = _run_budget(
        tmp_path, "rollback-export", "--state", str(state), "--output", str(export)
    )
    assert result.returncode == 0, result.stderr
    source = json.loads(export.read_text(encoding="utf-8"))
    binding = json.loads(
        export.with_suffix(export.suffix + ".binding.json").read_text(encoding="utf-8")
    )
    base_hash = str(binding["v2_sha256"])
    return state, export, base_hash, source


def _append_rollback_delta(source: dict[str, object]) -> dict[str, object]:
    source = copy.deepcopy(source)
    reservations = source["reservations"]
    assert isinstance(reservations, dict)
    reservations["eval-append"] = {
        "kind": "live-eval",
        "units": 2,
        "static_check": "contract-pass",
    }
    amendments = source["amendments"]
    assert isinstance(amendments, list)
    amendments.append(
        {
            "owner_approval": "Owner approved appended eval capacity.",
            "changes": {"max_live_eval_calls": 24},
        }
    )
    limits = source["limits"]
    assert isinstance(limits, dict)
    limits["max_live_eval_calls"] = 24
    usage = source["usage"]
    assert isinstance(usage, dict)
    usage["live_eval_calls"] = 2
    return source


def test_empty_rollback_base_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    export = tmp_path / "empty-rollback.json"
    exported = _run_budget(
        tmp_path, "rollback-export", "--state", str(state), "--output", str(export)
    )
    assert exported.returncode == 0, exported.stderr
    binding = json.loads(
        export.with_suffix(export.suffix + ".binding.json").read_text(encoding="utf-8")
    )
    assert binding["base_reservations"] == {}
    reconcile_args = (
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        binding["v2_sha256"],
    )
    reconciled = _run_budget(tmp_path, *reconcile_args)
    assert reconciled.returncode == 0, reconciled.stderr
    repeated = _run_budget(tmp_path, *reconcile_args)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["status"] == "already_reconciled"
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["reservations"] == {}


def test_rollback_export_is_a_v1_document_with_external_binding_roundtrip(
    tmp_path: Path,
) -> None:
    state, export, _, original = _rollback_fixture(tmp_path)
    projected = json.loads(export.read_text(encoding="utf-8"))
    assert set(projected) == {
        "schema_version",
        "limits",
        "agents",
        "usage",
        "reservations",
        "amendments",
    }
    imported = budget._legacy_to_v2(projected)
    assert imported["limits"] == original["limits"]
    assert imported["reservations"] == projected["reservations"]
    assert budget._v1_projection(imported) == projected
    binding_path = export.with_suffix(export.suffix + ".binding.json")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    assert (
        binding["base_reservations"]
        == json.loads(state.read_text(encoding="utf-8"))["reservations"]
    )
    assert binding["v2_sha256"]


def test_rollback_reconciles_append_only_delta_and_preserves_execution_history(
    tmp_path: Path,
) -> None:
    state, export, base_hash, original = _rollback_fixture(tmp_path)
    source = _append_rollback_delta(original)
    source_usage = source["usage"]
    assert isinstance(source_usage, dict)
    source_usage["agent_ids"] = list(reversed(source_usage["agent_ids"]))
    export.write_text(json.dumps(source), encoding="utf-8")
    reconciled = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert reconciled.returncode == 0, reconciled.stderr
    contents = json.loads(state.read_text(encoding="utf-8"))
    assert contents["dispatch_frozen"] is False
    assert contents["limits"]["max_live_eval_calls"] == 24
    assert "eval-append" in contents["reservations"]
    assert "attempt-1" in contents["attempts"]
    duplicate = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert duplicate.returncode == 0, duplicate.stderr
    observation_rows = json.loads(state.read_text(encoding="utf-8"))["observations"]
    assert isinstance(observation_rows, list)
    reconciled_rows = [item for item in observation_rows if item["kind"] == "rollback_reconciled"]
    assert len(reconciled_rows) == 1
    assert reconciled_rows[0]["projection_hash"]


def test_rollback_export_freezes_new_dispatch_until_reconciliation(tmp_path: Path) -> None:
    state, export, base_hash, _ = _rollback_fixture(tmp_path)
    existing = _run_budget(
        tmp_path,
        "start",
        "--state",
        str(state),
        "--reservation-id",
        "review",
        "--attempt-id",
        "blocked-existing",
    )
    assert existing.returncode == 2
    assert "dispatch is frozen" in existing.stderr
    blocked = _run_budget(
        tmp_path,
        "reserve",
        "--state",
        str(state),
        "--id",
        "blocked-dispatch",
        "--kind",
        "live-eval",
        "--static-check",
        "contract-pass",
    )
    assert blocked.returncode == 2
    assert "dispatch is frozen" in blocked.stderr
    source = json.loads(export.read_text(encoding="utf-8"))
    binding_path = export.with_suffix(export.suffix + ".binding.json")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["v2_revision"] += 1
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    export.write_text(json.dumps(source), encoding="utf-8")
    revision_mismatch = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert revision_mismatch.returncode == 2


@pytest.mark.parametrize("alias", ("state", "lock", "journal"))
def test_rollback_export_rejects_canonical_storage_aliases(tmp_path: Path, alias: str) -> None:
    state, _, _, _ = _rollback_fixture(tmp_path)
    targets = {
        "state": state,
        "lock": state.with_suffix(state.suffix + ".lock"),
        "journal": state.with_suffix(state.suffix + ".journal"),
    }
    result = _run_budget(
        tmp_path,
        "rollback-export",
        "--state",
        str(state),
        "--output",
        str(targets[alias]),
    )
    assert result.returncode == 2
    assert "aliases canonical" in result.stderr


def test_rollback_export_rejects_symlink_alias_before_resolution(tmp_path: Path) -> None:
    state, _, _, _ = _rollback_fixture(tmp_path)
    alias = tmp_path / "rollback-alias.json"
    alias.symlink_to(state)
    result = _run_budget(
        tmp_path,
        "rollback-export",
        "--state",
        str(state),
        "--output",
        str(alias),
    )
    assert result.returncode == 2
    assert "symlink" in result.stderr


@pytest.mark.parametrize("mutation", ("reservation", "deletion", "counter", "amendment"))
def test_rollback_rejects_mutations_deletions_and_counter_or_amendment_mismatch(
    tmp_path: Path, mutation: str
) -> None:
    state, export, base_hash, original = _rollback_fixture(tmp_path)
    source = _append_rollback_delta(original)
    if mutation == "reservation":
        cast_reservations = source["reservations"]
        assert isinstance(cast_reservations, dict)
        review = cast_reservations["review"]
        assert isinstance(review, dict)
        review["scope"] = "mutated"
    elif mutation == "deletion":
        cast_reservations = source["reservations"]
        assert isinstance(cast_reservations, dict)
        del cast_reservations["review"]
    elif mutation == "counter":
        cast_usage = source["usage"]
        assert isinstance(cast_usage, dict)
        cast_usage["live_eval_calls"] = 1
    else:
        cast_amendments = source["amendments"]
        assert isinstance(cast_amendments, list)
        amendment = cast_amendments[-1]
        assert isinstance(amendment, dict)
        amendment["changes"] = {"max_fast_gates": 2}
    export.write_text(json.dumps(source), encoding="utf-8")
    result = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert result.returncode == 2
    assert json.loads(state.read_text(encoding="utf-8"))["dispatch_frozen"] is True


def test_rollback_rejects_unbound_or_mismatched_canonical_base(tmp_path: Path) -> None:
    state, export, base_hash, original = _rollback_fixture(tmp_path)
    unbound = copy.deepcopy(original)
    export.write_text(json.dumps(unbound), encoding="utf-8")
    export.with_suffix(export.suffix + ".binding.json").unlink()
    missing_binding = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert missing_binding.returncode == 2

    export.write_text(json.dumps(original), encoding="utf-8")
    mismatched = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        "wrong-base",
    )
    assert mismatched.returncode == 2


@pytest.mark.parametrize(
    "operation",
    [
        ["finish", "--attempt-id", "attempt-1", "--status", "succeeded", "--result-code", "ok"],
        ["reconcile", "--attempt-id", "attempt-1"],
        ["amend", "--owner-approval", "test", "--max-fast-gates", "3"],
        [
            "recover",
            "--trigger-id",
            "missing",
            "--next-attempt-id",
            "new",
            "--targeted-check-id",
            "check",
            "--targeted-check-result",
            "passed",
            "--next-action",
            "retry",
        ],
    ],
)
def test_rollback_freeze_preserves_base_across_mutating_commands(
    tmp_path: Path, operation: list[str]
) -> None:
    state, export, base_hash, _ = _rollback_fixture(tmp_path)
    before = state.read_bytes()
    result = _run_budget(tmp_path, *operation, "--state", str(state))
    assert result.returncode == 2, result.stdout
    assert "frozen" in result.stderr
    assert state.read_bytes() == before
    resumed = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert resumed.returncode == 0, resumed.stderr


def test_rollback_export_rejects_running_attempt_without_freezing(tmp_path: Path) -> None:
    state = _init_state(tmp_path)
    for args in [
        ["reserve", "--id", "gate", "--kind", "fast-gate"],
        ["start", "--reservation-id", "gate", "--attempt-id", "running"],
    ]:
        result = _run_budget(tmp_path, *args, "--state", str(state))
        assert result.returncode == 0, result.stderr
    before = state.read_bytes()
    exported = _run_budget(
        tmp_path,
        "rollback-export",
        "--state",
        str(state),
        "--output",
        str(tmp_path / "export.json"),
    )
    assert exported.returncode == 2
    assert "running attempts" in exported.stderr
    assert state.read_bytes() == before


@pytest.mark.parametrize("operation", ["status", "import", "rollback-reconcile"])
def test_oversized_json_inputs_are_rejected_before_decode(tmp_path: Path, operation: str) -> None:
    state = _init_state(tmp_path)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (budget.MAX_LEDGER_BYTES + 1))
    if operation == "status":
        args = ["status", "--state", str(oversized)]
    else:
        args = [operation, "--state", str(state), "--source", str(oversized)]
        if operation == "rollback-reconcile":
            args += ["--base-hash", "missing"]
    result = _run_budget(tmp_path, *args)
    assert result.returncode == 2
    assert "exceeds bounded size" in result.stderr


def test_boundary_length_ids_remain_persistable_when_triggers_are_created(tmp_path: Path) -> None:
    state = _init_state(tmp_path, "--max-fast-gates", "2")
    for index in range(3):
        identifier = str(index) + "x" * (budget.MAX_FIELD_BYTES - 1)
        reserved = _run_budget(
            tmp_path, "reserve", "--state", str(state), "--id", identifier, "--kind", "fast-gate"
        )
        assert reserved.returncode == 0, reserved.stderr
        if index < 2:
            started = _run_budget(
                tmp_path,
                "start",
                "--state",
                str(state),
                "--reservation-id",
                identifier,
                "--attempt-id",
                identifier,
            )
            assert started.returncode == 0, started.stderr
            finished = _run_budget(
                tmp_path,
                "finish",
                "--state",
                str(state),
                "--attempt-id",
                identifier,
                "--status",
                "failed",
                "--result-code",
                "failure",
            )
            assert finished.returncode == 0, finished.stderr
    triggers = json.loads(state.read_text(encoding="utf-8"))["triggers"]
    assert {item["kind"] for item in triggers.values()} == {"budget_overage", "repeated_failure"}
    assert all(len(key.encode()) <= budget.MAX_FIELD_BYTES for key in triggers)


def test_interrupted_rollback_merge_recovers_and_replays_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, export, base_hash, original = _rollback_fixture(tmp_path)
    export.write_text(json.dumps(_append_rollback_delta(original)), encoding="utf-8")
    calls = 0
    original_fsync = os.fsync

    def fail_state_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted merge")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_state_fsync)
    parsed = budget._parser().parse_args(
        [
            "rollback-reconcile",
            "--state",
            str(state),
            "--source",
            str(export),
            "--base-hash",
            base_hash,
        ]
    )
    with pytest.raises(budget.BudgetError, match="uncertain"):
        budget._rollback_reconcile(parsed)
    monkeypatch.setattr(os, "fsync", original_fsync)
    retried = _run_budget(
        tmp_path,
        "rollback-reconcile",
        "--state",
        str(state),
        "--source",
        str(export),
        "--base-hash",
        base_hash,
    )
    assert retried.returncode == 0, retried.stderr
    assert json.loads(state.read_text(encoding="utf-8"))["dispatch_frozen"] is False
