#!/usr/bin/env python3
"""Maintain a bounded, crash-durable orchestration ledger.

Reservations describe planned work; attempts describe observed work.  The ledger contains only
bounded, safe metadata and never stores prompts, provider output, credentials, or stderr.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TextIO, TypedDict, cast

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DEFAULT_MAX_AGENT_THREADS = 8
DEFAULT_MAX_LIVE_EVAL_CALLS = 12
DEFAULT_MAX_REVIEW_ROUNDS_PER_SCOPE = 2
DEFAULT_MAX_FAST_GATES = 6
DEFAULT_MAX_INTEGRATION_CHECKS = 2
MAX_HISTORY_TURNS = 4
MAX_ATTEMPTS = 10000
MAX_REPORT_BYTES = 512 * 1024
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_FIELD_BYTES = 2000
TOKEN_FIELDS = {"input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens"}
TOKEN_FIELD_ORDER = (
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
)
V1_RESERVATION_FIELDS = (
    "kind",
    "units",
    "agent_id",
    "fork_turns",
    "history_exception",
    "scope",
    "static_check",
    "alerts",
)
USAGE_SOURCES = {"codex_event_stream", "unavailable", "native_agent", "app"}
USAGE_CHANNELS = {"stdout_jsonl", "native_agent", "app", "permission_reviewer", "human"}
CATEGORY_VALUES = {"model", "gate", "lease", "permission", "human"}
GIT_BINARY = shutil.which("git")
TERMINAL_STATUSES = {"succeeded", "failed", "interrupted"}
LimitField = Literal[
    "max_agent_threads",
    "max_live_eval_calls",
    "max_review_rounds_per_scope",
    "max_fast_gates",
    "max_integration_checks",
]
LIMIT_FIELDS: tuple[LimitField, ...] = (
    "max_agent_threads",
    "max_live_eval_calls",
    "max_review_rounds_per_scope",
    "max_fast_gates",
    "max_integration_checks",
)


class BudgetError(Exception):
    """Raised when a budget operation cannot safely proceed."""


class Limits(TypedDict):
    max_agent_threads: int
    max_live_eval_calls: int
    max_review_rounds_per_scope: int
    max_fast_gates: int
    max_integration_checks: int


class Agent(TypedDict):
    fork_turns: str
    history_exception: str | None


class Usage(TypedDict):
    agent_ids: list[str]
    live_eval_calls: int
    review_rounds_by_scope: dict[str, int]
    fast_gates: int
    integration_checks: int


class Token(TypedDict):
    coverage: Literal["measured", "unavailable"]
    value: int | None


Reservation = dict[str, object]
Attempt = dict[str, object]


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _bounded(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_FIELD_BYTES:
        raise argparse.ArgumentTypeError("must be bounded and non-empty")
    return normalized


def _default_limits() -> Limits:
    return {
        "max_agent_threads": DEFAULT_MAX_AGENT_THREADS,
        "max_live_eval_calls": DEFAULT_MAX_LIVE_EVAL_CALLS,
        "max_review_rounds_per_scope": DEFAULT_MAX_REVIEW_ROUNDS_PER_SCOPE,
        "max_fast_gates": DEFAULT_MAX_FAST_GATES,
        "max_integration_checks": DEFAULT_MAX_INTEGRATION_CHECKS,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value: str | None, label: str) -> str:
    result = _utc_now() if value is None else value
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BudgetError(f"invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BudgetError(f"invalid {label}: UTC offset required")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _instant(value: object, label: str) -> float:
    if not isinstance(value, str):
        raise BudgetError(f"invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BudgetError(f"invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BudgetError(f"invalid {label}: UTC offset required")
    return parsed.timestamp()


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BudgetError(f"invalid budget state: {label} must be an object")
    return value


def _require_int(value: object, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise BudgetError(f"invalid budget state: {label} must be an integer >= {minimum}")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_FIELD_BYTES:
        raise BudgetError(f"invalid budget state: {label} must be bounded text")
    return value


def _reject_unknown(mapping: dict[str, object], allowed: set[str], label: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise BudgetError(f"invalid budget state: {label} has unknown fields")


def _base_state(limits: Limits | None = None) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": limits or _default_limits(),
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
        "attempts": {},
        "recoveries": {},
        "triggers": {},
        "observations": [],
        "imports": [],
        "revision": 0,
    }


def _source_commit(path: Path) -> str | None:
    try:
        if GIT_BINARY is None:
            return None
        result = subprocess.run(  # noqa: S603 -- fixed git executable and argument vector.
            [GIT_BINARY, "-C", str(path.parent), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _source_label(path: Path) -> str:
    if GIT_BINARY is not None:
        try:
            result = subprocess.run(  # noqa: S603 -- fixed git executable and argument vector.
                [GIT_BINARY, "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                return path.resolve().relative_to(Path(result.stdout.strip()).resolve()).as_posix()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return str(path)


def _committed_blob(worktree: Path, relative: str) -> bytes | None:
    if GIT_BINARY is None:
        raise BudgetError("git executable was not found")
    try:
        size = subprocess.run(  # noqa: S603 -- fixed git executable and object path.
            [GIT_BINARY, "-C", str(worktree), "cat-file", "-s", f"HEAD:{relative}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if size.returncode != 0:
            return None
        if int(size.stdout.strip()) > MAX_LEDGER_BYTES:
            raise BudgetError("committed legacy budget source exceeds bounded size")
        result = subprocess.run(  # noqa: S603 -- fixed git executable and path.
            [GIT_BINARY, "-C", str(worktree), "show", f"HEAD:{relative}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BudgetError("cannot inspect committed legacy budget source") from exc
    if result.returncode != 0:
        return None
    if len(result.stdout) > MAX_LEDGER_BYTES:
        raise BudgetError("committed legacy budget source exceeds bounded size")
    return result.stdout


def _legacy_to_v2(raw: dict[str, object], source: Path | None = None) -> dict[str, object]:
    """Import v1 as an immutable source; missing execution is explicitly unresolved."""
    if raw.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise BudgetError("unsupported budget state schema")
    state = _base_state()
    for key in ("limits", "agents", "usage", "reservations", "amendments"):
        if key in raw:
            state[key] = copy.deepcopy(raw[key])
    state["observations"] = [{"kind": "legacy_unresolved", "blocking": False, "source": "v1"}]
    if source is not None:
        try:
            digest = hashlib.sha256(_read_bounded_bytes(source, "legacy source")).hexdigest()
        except (BudgetError, OSError) as exc:
            raise BudgetError("cannot fingerprint legacy budget state") from exc
        state["imports"] = [
            {
                "path": _source_label(source),
                "sha256": digest,
                "source_commit": _source_commit(source),
            }
        ]
    return _validate_state(state)


def _validate_state(raw: object) -> dict[str, object]:
    root = _require_mapping(raw, "root")
    _reject_unknown(
        root,
        {
            "schema_version",
            "limits",
            "agents",
            "usage",
            "reservations",
            "amendments",
            "attempts",
            "recoveries",
            "triggers",
            "observations",
            "imports",
            "revision",
            "dispatch_frozen",
        },
        "root",
    )
    version = root.get("schema_version")
    if version == LEGACY_SCHEMA_VERSION:
        return _legacy_to_v2(root)
    if version != SCHEMA_VERSION:
        raise BudgetError("unsupported budget state schema")
    limits_raw = _require_mapping(root.get("limits"), "limits")
    _reject_unknown(limits_raw, set(LIMIT_FIELDS), "limits")
    limits: Limits = {
        "max_agent_threads": _require_int(
            limits_raw.get("max_agent_threads"), "limits.max_agent_threads", 1
        ),
        "max_live_eval_calls": _require_int(
            limits_raw.get("max_live_eval_calls"), "limits.max_live_eval_calls", 1
        ),
        "max_review_rounds_per_scope": _require_int(
            limits_raw.get("max_review_rounds_per_scope"),
            "limits.max_review_rounds_per_scope",
            1,
        ),
        "max_fast_gates": _require_int(
            limits_raw.get("max_fast_gates"), "limits.max_fast_gates", 1
        ),
        "max_integration_checks": _require_int(
            limits_raw.get("max_integration_checks"), "limits.max_integration_checks", 1
        ),
    }
    agents_raw = _require_mapping(root.get("agents"), "agents")
    if len(agents_raw) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: agents exceeds bound")
    agents: dict[str, Agent] = {}
    for agent_id, value in agents_raw.items():
        _require_string(agent_id, f"agents.{agent_id}")
        agent = _require_mapping(value, f"agents.{agent_id}")
        _reject_unknown(agent, {"fork_turns", "history_exception"}, f"agents.{agent_id}")
        fork = _require_string(agent.get("fork_turns"), f"agents.{agent_id}.fork_turns")
        if fork not in {"unknown", "none", "all"}:
            try:
                parsed_fork = int(fork)
            except ValueError as exc:
                raise BudgetError(f"invalid budget state: agents.{agent_id}.fork_turns") from exc
            if not 1 <= parsed_fork <= MAX_HISTORY_TURNS:
                raise BudgetError(f"invalid budget state: agents.{agent_id}.fork_turns")
        exception = agent.get("history_exception")
        if exception is not None:
            _require_string(exception, f"agents.{agent_id}.history_exception")
        agents[agent_id] = {"fork_turns": fork, "history_exception": cast(str | None, exception)}
    usage_raw = _require_mapping(root.get("usage"), "usage")
    _reject_unknown(
        usage_raw,
        {
            "agent_ids",
            "live_eval_calls",
            "review_rounds_by_scope",
            "fast_gates",
            "integration_checks",
        },
        "usage",
    )
    ids = usage_raw.get("agent_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise BudgetError("invalid budget state: usage.agent_ids must be string list")
    if len(ids) != len(set(ids)) or set(ids) != set(agents):
        raise BudgetError("invalid budget state: agents and usage.agent_ids disagree")
    if len(ids) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: usage.agent_ids exceeds bound")
    rounds = _require_mapping(usage_raw.get("review_rounds_by_scope"), "review rounds")
    if len(rounds) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: review rounds exceeds bound")
    usage: Usage = {
        "agent_ids": ids,
        "live_eval_calls": _require_int(usage_raw.get("live_eval_calls"), "live eval calls"),
        "review_rounds_by_scope": {
            scope: _require_int(value, f"review rounds for {scope}")
            for scope, value in rounds.items()
        },
        "fast_gates": _require_int(usage_raw.get("fast_gates"), "fast gates"),
        "integration_checks": _require_int(
            usage_raw.get("integration_checks"), "integration checks"
        ),
    }
    reservations_raw = _require_mapping(root.get("reservations"), "reservations")
    if len(reservations_raw) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: reservations exceeds bound")
    reservations: dict[str, Reservation] = {}
    for rid, value in reservations_raw.items():
        _require_string(rid, f"reservations.{rid}")
        reservation = _require_mapping(value, f"reservations.{rid}")
        _reject_unknown(
            reservation,
            {
                "kind",
                "units",
                "agent_id",
                "fork_turns",
                "scope",
                "static_check",
                "stage",
                "model",
                "effort",
                "category",
                "retry_of",
                "reason",
                "history_exception",
                "refresh_item_id",
                "refresh_worktree",
                "refresh_previous_attempt",
                "refresh_previous_tree",
                "refresh_previous_base",
                "refresh_current_tree",
                "refresh_current_base",
                "alerts",
            },
            f"reservations.{rid}",
        )
        kind = _require_string(reservation.get("kind"), f"reservations.{rid}.kind")
        if kind not in {
            "agent",
            "review",
            "review-refresh",
            "live-eval",
            "fast-gate",
            "integration-check",
        }:
            raise BudgetError(f"invalid budget state: reservations.{rid}.kind")
        _require_int(reservation.get("units"), f"reservations.{rid}.units", 1)
        for field in set(reservation) - {"kind", "units", "alerts"}:
            if reservation[field] is not None:
                _require_string(reservation[field], f"reservations.{rid}.{field}")
        if (
            reservation.get("category") is not None
            and reservation["category"] not in CATEGORY_VALUES
        ):
            raise BudgetError(f"invalid category: reservations.{rid}")
        alerts = reservation.get("alerts")
        if alerts is not None:
            if not isinstance(alerts, list) or len(alerts) > 32:
                raise BudgetError(f"invalid budget state: reservations.{rid}.alerts")
            for index, alert in enumerate(alerts):
                _require_string(alert, f"reservations.{rid}.alerts[{index}]")
        reservations[rid] = reservation
    derived_usage = _usage_from_reservations(reservations)
    if (
        usage["live_eval_calls"] != derived_usage["live_eval_calls"]
        or usage["review_rounds_by_scope"] != derived_usage["review_rounds_by_scope"]
        or usage["fast_gates"] != derived_usage["fast_gates"]
        or usage["integration_checks"] != derived_usage["integration_checks"]
        or set(usage["agent_ids"]) != set(derived_usage["agent_ids"])
    ):
        raise BudgetError("invalid budget state: usage counters are not reservation-derived")
    attempts_raw = _require_mapping(root.get("attempts", {}), "attempts")
    if len(attempts_raw) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: attempts exceeds bound")
    attempts: dict[str, Attempt] = {}
    for attempt_id, value in attempts_raw.items():
        _require_string(attempt_id, f"attempts.{attempt_id}")
        attempt = _require_mapping(value, f"attempts.{attempt_id}")
        _reject_unknown(
            attempt,
            {
                "attempt_id",
                "reservation_id",
                "status",
                "started_at",
                "ended_at",
                "elapsed_ms",
                "result_code",
                "stage",
                "model",
                "effort",
                "category",
                "retry_of",
                "retry_diagnosis",
                "tokens",
                "usage_integrity",
                "usage_source",
                "usage_channels",
            },
            f"attempts.{attempt_id}",
        )
        if attempt.get("attempt_id") != attempt_id:
            raise BudgetError(f"invalid budget state: attempts.{attempt_id}.attempt_id")
        status = _require_string(attempt.get("status"), f"attempts.{attempt_id}.status")
        if status not in {"running", *TERMINAL_STATUSES}:
            raise BudgetError(f"invalid budget state: attempts.{attempt_id}.status")
        reservation_id = _require_string(
            attempt.get("reservation_id"), f"attempts.{attempt_id}.reservation_id"
        )
        if reservation_id not in reservations:
            raise BudgetError(
                f"invalid budget state: attempts.{attempt_id} has unknown reservation"
            )
        _require_string(attempt.get("started_at"), f"attempts.{attempt_id}.started_at")
        attempt["started_at"] = _timestamp(
            cast(str, attempt["started_at"]), f"attempts.{attempt_id}.started_at"
        )
        if status != "running":
            _require_string(attempt.get("ended_at"), f"attempts.{attempt_id}.ended_at")
            attempt["ended_at"] = _timestamp(
                cast(str, attempt["ended_at"]), f"attempts.{attempt_id}.ended_at"
            )
            _require_string(attempt.get("result_code"), f"attempts.{attempt_id}.result_code")
        elapsed = attempt.get("elapsed_ms")
        if elapsed is not None:
            _require_int(elapsed, f"attempts.{attempt_id}.elapsed_ms")
        for field in {
            "result_code",
            "stage",
            "model",
            "effort",
            "category",
            "retry_of",
            "retry_diagnosis",
        }:
            if attempt.get(field) is not None:
                _require_string(attempt[field], f"attempts.{attempt_id}.{field}")
        if attempt.get("category") is not None and attempt["category"] not in CATEGORY_VALUES:
            raise BudgetError(f"invalid category: attempts.{attempt_id}")
        integrity_value = attempt.get("usage_integrity")
        if integrity_value is not None:
            integrity = _require_string(integrity_value, f"attempts.{attempt_id}.usage_integrity")
            if integrity not in {
                "telemetry_overflow",
                "timeout",
                "malformed_usage",
                "regressive_usage",
                "interrupted",
                "process_io_error",
            } and not (integrity.startswith("exit_") and integrity[5:].lstrip("-").isdigit()):
                raise BudgetError(f"invalid budget state: attempts.{attempt_id}.usage_integrity")
        usage_source = attempt.get("usage_source")
        if usage_source is not None:
            _require_string(usage_source, f"attempts.{attempt_id}.usage_source")
            if usage_source not in USAGE_SOURCES:
                raise BudgetError(f"invalid usage source: attempts.{attempt_id}")
        usage_channels = attempt.get("usage_channels", [])
        if not isinstance(usage_channels, list) or len(usage_channels) > 8:
            raise BudgetError(f"invalid usage channels: attempts.{attempt_id}")
        for channel in usage_channels:
            _require_string(channel, f"attempts.{attempt_id}.usage_channels")
            if channel not in USAGE_CHANNELS:
                raise BudgetError(f"invalid usage channel: attempts.{attempt_id}")
        tokens = attempt.get("tokens", {})
        if status != "running" and not isinstance(tokens, dict):
            raise BudgetError(f"invalid budget state: attempts.{attempt_id}.tokens")
        if tokens is not None:
            token_map = _require_mapping(tokens, f"attempts.{attempt_id}.tokens")
            _reject_unknown(
                token_map,
                TOKEN_FIELDS,
                f"attempts.{attempt_id}.tokens",
            )
            if status != "running" and set(token_map) != TOKEN_FIELDS:
                raise BudgetError(
                    f"invalid budget state: attempts.{attempt_id}.tokens is incomplete"
                )
            for token_name, token in token_map.items():
                token_value = _require_mapping(token, f"attempts.{attempt_id}.tokens.{token_name}")
                _reject_unknown(
                    token_value,
                    {"value", "coverage"},
                    f"attempts.{attempt_id}.tokens.{token_name}",
                )
                coverage = _require_string(
                    token_value.get("coverage"),
                    f"attempts.{attempt_id}.tokens.{token_name}.coverage",
                )
                if coverage not in {"measured", "unavailable"}:
                    raise BudgetError(f"invalid token coverage: {token_name}")
                if coverage == "measured":
                    _require_int(
                        token_value.get("value"), f"attempts.{attempt_id}.tokens.{token_name}.value"
                    )
                elif token_value.get("value") is not None:
                    raise BudgetError(
                        f"invalid unavailable token value: attempts.{attempt_id}.{token_name}"
                    )
            input_value = token_map.get("input_tokens")
            cached_value = token_map.get("cached_input_tokens")
            if (
                isinstance(input_value, dict)
                and isinstance(cached_value, dict)
                and input_value.get("coverage") == cached_value.get("coverage") == "measured"
                and int(cached_value["value"]) > int(input_value["value"])
            ):
                raise BudgetError(f"invalid token relationship in attempt: {attempt_id}")
            uncached_value = token_map.get("uncached_input_tokens")
            if (
                isinstance(input_value, dict)
                and isinstance(cached_value, dict)
                and isinstance(uncached_value, dict)
                and input_value.get("coverage")
                == cached_value.get("coverage")
                == uncached_value.get("coverage")
                == "measured"
                and int(uncached_value["value"])
                != int(input_value["value"]) - int(cached_value["value"])
            ):
                raise BudgetError(f"invalid uncached input relationship in attempt: {attempt_id}")
        if status == "running" and ("ended_at" in attempt or "result_code" in attempt):
            raise BudgetError(f"invalid running lifecycle fields: attempts.{attempt_id}")
        attempts[attempt_id] = attempt
    recoveries = _require_mapping(root.get("recoveries", {}), "recoveries")
    if len(recoveries) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: recoveries exceeds bound")
    for recovery_id, value in recoveries.items():
        _require_string(recovery_id, f"recoveries.{recovery_id}")
        recovery = _require_mapping(value, f"recoveries.{recovery_id}")
        _reject_unknown(
            recovery,
            {
                "trigger_id",
                "trigger_ids",
                "next_attempt_id",
                "targeted_check_id",
                "targeted_check_result",
                "next_action",
                "consumed",
                "created_at",
            },
            f"recoveries.{recovery_id}",
        )
        for field in (
            "trigger_id",
            "next_attempt_id",
            "targeted_check_id",
            "next_action",
            "created_at",
        ):
            _require_string(recovery.get(field), f"recoveries.{recovery_id}.{field}")
        trigger_ids = recovery.get("trigger_ids")
        if trigger_ids is None:
            trigger_ids = [recovery["trigger_id"]]
            recovery["trigger_ids"] = trigger_ids
        if (
            not isinstance(trigger_ids, list)
            or not trigger_ids
            or len(trigger_ids) > MAX_ATTEMPTS
            or any(not isinstance(trigger_id, str) or not trigger_id for trigger_id in trigger_ids)
            or recovery["trigger_id"] not in trigger_ids
        ):
            raise BudgetError(f"invalid budget state: recoveries.{recovery_id}.trigger_ids")
        recovery["created_at"] = _timestamp(
            cast(str, recovery["created_at"]), f"recoveries.{recovery_id}.created_at"
        )
        if recovery.get("next_attempt_id") != recovery_id:
            raise BudgetError(f"invalid budget state: recoveries.{recovery_id}.next_attempt_id")
        if recovery.get("targeted_check_result") != "passed":
            raise BudgetError(
                f"invalid budget state: recoveries.{recovery_id}.targeted_check_result"
            )
        if not isinstance(recovery.get("consumed"), bool):
            raise BudgetError(f"invalid budget state: recoveries.{recovery_id}.consumed")
    triggers = _require_mapping(root.get("triggers", {}), "triggers")
    if len(triggers) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: triggers exceeds bound")
    for trigger_id, value in triggers.items():
        trigger = _require_mapping(value, f"triggers.{trigger_id}")
        _reject_unknown(
            trigger,
            {
                "trigger_id",
                "kind",
                "reservation_id",
                "blocking",
                "created_at",
                "signature",
                "attempt_id",
                "resolved_at",
                "resolved_by",
            },
            f"triggers.{trigger_id}",
        )
        if trigger.get("trigger_id") != trigger_id:
            raise BudgetError(f"invalid budget state: triggers.{trigger_id}.trigger_id")
        if trigger.get("kind") not in {"budget_overage", "repeated_failure"}:
            raise BudgetError(f"invalid budget state: triggers.{trigger_id}.kind")
        if not isinstance(trigger.get("blocking"), bool):
            raise BudgetError(f"invalid budget state: triggers.{trigger_id}.blocking")
        _require_string(trigger.get("created_at"), f"triggers.{trigger_id}.created_at")
        trigger["created_at"] = _timestamp(
            cast(str, trigger["created_at"]), f"triggers.{trigger_id}.created_at"
        )
        for field in ("reservation_id", "attempt_id"):
            if trigger.get(field) is not None:
                _require_string(trigger[field], f"triggers.{trigger_id}.{field}")
        signature = trigger.get("signature")
        if signature is not None:
            if not isinstance(signature, list) or len(signature) != 4:
                raise BudgetError(f"invalid budget state: triggers.{trigger_id}.signature")
            for value in signature:
                if value is not None:
                    _require_string(value, f"triggers.{trigger_id}.signature")
        if trigger.get("kind") == "budget_overage" and trigger.get("reservation_id") is None:
            raise BudgetError(f"invalid budget state: triggers.{trigger_id}.reservation_id")
        if trigger.get("kind") == "repeated_failure" and (
            trigger.get("attempt_id") is None or signature is None
        ):
            raise BudgetError(
                f"invalid budget state: triggers.{trigger_id} repeated-failure binding"
            )
        for field in ("resolved_at", "resolved_by"):
            if trigger.get(field) is not None:
                _require_string(trigger[field], f"triggers.{trigger_id}.{field}")
    for attempt_id, attempt in attempts.items():
        parent = cast(str | None, attempt.get("retry_of"))
        chain: set[str] = set()
        while parent is not None:
            if parent in chain or parent == attempt_id:
                raise BudgetError(f"invalid retry cycle: attempts.{attempt_id}")
            chain.add(parent)
            parent_attempt = attempts.get(parent)
            if parent_attempt is None or parent_attempt.get("status") not in TERMINAL_STATUSES:
                raise BudgetError(f"invalid retry parent: attempts.{attempt_id}")
            parent = cast(str | None, parent_attempt.get("retry_of"))
    amendments = root.get("amendments")
    if not isinstance(amendments, list) or len(amendments) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: amendments must be a list")
    for index, amendment in enumerate(amendments):
        item = _require_mapping(amendment, f"amendments[{index}]")
        _reject_unknown(item, {"owner_approval", "changes"}, f"amendments[{index}]")
        _require_string(item.get("owner_approval"), f"amendments[{index}].owner_approval")
        changes = _require_mapping(item.get("changes"), f"amendments[{index}].changes")
        for field, value in changes.items():
            if field not in LIMIT_FIELDS:
                raise BudgetError(f"invalid budget state: amendments[{index}].changes")
            _require_int(value, f"amendments[{index}].changes.{field}", 1)
    observations = root.get("observations", [])
    if not isinstance(observations, list) or len(observations) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: observations must be a bounded list")
    for index, observation in enumerate(observations):
        item = _require_mapping(observation, f"observations[{index}]")
        _reject_unknown(
            item,
            {"kind", "blocking", "source", "base_hash", "projection_hash", "coverage"},
            f"observations[{index}]",
        )
        _require_string(item.get("kind"), f"observations[{index}].kind")
        for field in ("source", "base_hash", "projection_hash", "coverage"):
            if item.get(field) is not None:
                _require_string(item[field], f"observations[{index}].{field}")
        if item.get("blocking") is not None and not isinstance(item["blocking"], bool):
            raise BudgetError(f"invalid budget state: observations[{index}].blocking")
    imports = root.get("imports", [])
    if not isinstance(imports, list) or len(imports) > MAX_ATTEMPTS:
        raise BudgetError("invalid budget state: imports must be a bounded list")
    for index, imported in enumerate(imports):
        item = _require_mapping(imported, f"imports[{index}]")
        _reject_unknown(item, {"path", "sha256", "source_commit"}, f"imports[{index}]")
        _require_string(item.get("path"), f"imports[{index}].path")
        _require_string(item.get("sha256"), f"imports[{index}].sha256")
        if item.get("source_commit") is not None:
            _require_string(item["source_commit"], f"imports[{index}].source_commit")
    state = copy.deepcopy(root)
    state.update(
        {
            "schema_version": SCHEMA_VERSION,
            "limits": limits,
            "agents": agents,
            "usage": usage,
            "reservations": reservations,
        }
    )
    state.setdefault("attempts", {})
    state.setdefault("recoveries", {})
    state.setdefault("triggers", {})
    state.setdefault("observations", [])
    state.setdefault("imports", [])
    state.setdefault("revision", 0)
    _require_int(state["revision"], "revision")
    if "dispatch_frozen" in state and not isinstance(state["dispatch_frozen"], bool):
        raise BudgetError("invalid budget state: dispatch_frozen must be boolean")
    state["attempts"] = attempts
    return state


def _load_state(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(_read_bounded_bytes(path, "budget state"))
    except OSError as exc:
        raise BudgetError(f"cannot read budget state: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError(f"invalid budget state JSON: {path}") from exc
    return _validate_state(raw)


def _read_bounded_bytes(path: Path, label: str) -> bytes:
    try:
        with path.open("rb") as file:
            payload = file.read(MAX_LEDGER_BYTES + 1)
    except OSError as exc:
        raise BudgetError(f"cannot read {label}: {path}") from exc
    if len(payload) > MAX_LEDGER_BYTES:
        raise BudgetError(f"{label} exceeds bounded size")
    return payload


def _sha_state(state: dict[str, object]) -> str:
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _recover_journal(path: Path) -> None:
    journal = path.with_suffix(path.suffix + ".journal")
    if not journal.exists():
        return
    try:
        payload = json.loads(_read_bounded_bytes(journal, "ledger journal"))
        previous = payload["previous_hash"]
        intended = payload["intended_hash"]
        current = (
            hashlib.sha256(_read_bounded_bytes(path, "budget state")).hexdigest()
            if path.exists()
            else None
        )
    except (OSError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError(
            "ambiguous ledger journal; explicit storage reconciliation required"
        ) from exc
    if current not in {previous, intended}:
        raise BudgetError("ambiguous ledger journal; explicit storage reconciliation required")
    try:
        journal.unlink()
        _fsync_dir(path.parent)
    except OSError as exc:
        raise BudgetError("cannot clear recovered ledger journal") from exc


def _write_state(path: Path, state: dict[str, object]) -> None:
    """Journal hashes and fsync every directory/state boundary before acknowledging a mutation."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_hash = (
        hashlib.sha256(_read_bounded_bytes(path, "budget state")).hexdigest()
        if path.exists()
        else None
    )
    state = _validate_state(copy.deepcopy(state))
    state["revision"] = _require_int(state.get("revision", 0), "revision") + 1
    serialized = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(serialized.encode("utf-8")) > MAX_LEDGER_BYTES:
        raise BudgetError("budget state exceeds bounded size")
    intended_hash = hashlib.sha256(serialized.encode()).hexdigest()
    journal = path.with_suffix(path.suffix + ".journal")
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.journal.",
            delete=False,
        ) as file:
            json.dump(
                {"version": 1, "previous_hash": previous_hash, "intended_hash": intended_hash}, file
            )
            file.flush()
            os.fsync(file.fileno())
            journal_tmp = Path(file.name)
        os.replace(journal_tmp, journal)
        _fsync_dir(path.parent)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
            state_tmp = Path(file.name)
        os.chmod(state_tmp, 0o600)
        os.replace(state_tmp, path)
        _fsync_dir(path.parent)
        journal.unlink()
        _fsync_dir(path.parent)
    except OSError as exc:
        raise BudgetError("ledger storage result is uncertain; dispatch is forbidden") from exc


def _with_lock(path: Path) -> TextIO:
    if path.is_symlink() or path.parent.is_symlink():
        raise BudgetError("budget state and its parent cannot be symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        lock_file = lock_path.open("a", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        _recover_journal(path)
    except (OSError, BudgetError) as exc:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except (UnboundLocalError, OSError):
            pass
        if isinstance(exc, BudgetError):
            raise
        raise BudgetError(f"cannot create budget lock: {lock_path}") from exc
    return lock_file


def _release_lock(lock_file: TextIO) -> None:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    lock_file.close()


def _git_common_dir(worktree: Path) -> Path:
    try:
        if GIT_BINARY is None:
            raise BudgetError("git executable was not found")
        result = subprocess.run(  # noqa: S603 -- fixed git executable and argument vector.
            [GIT_BINARY, "-C", str(worktree), "rev-parse", "--git-common-dir"],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        common = Path(result.stdout.strip())
        return (common if common.is_absolute() else worktree / common).resolve(strict=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BudgetError("cannot resolve Git common directory") from exc


def canonical_state_path(worktree: Path, item_id: str) -> Path:
    """Resolve one candidate-independent state path and reject unsafe item identities."""
    if not item_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for char in item_id
    ):
        raise BudgetError("item id contains unsafe path characters")
    common = _git_common_dir(worktree.resolve())
    root = common / "nadili-orchestration"
    if root.is_symlink():
        raise BudgetError("unsafe orchestration state root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    if root.is_symlink() or not root.resolve().parent.samefile(common):
        raise BudgetError("unsafe orchestration state root")
    path = root / f"{item_id}.json"
    if path.exists() and path.is_symlink():
        raise BudgetError("canonical state cannot be a symlink")
    return path


def _parse_fork_turns(args: argparse.Namespace, require_fresh: bool) -> tuple[str, list[str]]:
    alerts: list[str] = []
    fork = args.fork_turns
    if fork is None:
        return "unknown", ["missing fork_turns; use none by default"]
    if fork == "all":
        alerts.append('fork_turns="all" defeats the context budget')
    if require_fresh and fork != "none":
        alerts.append('reviewer is not fresh-context; use fork_turns="none"')
    if fork == "none":
        if args.history_exception is not None:
            alerts.append("history exception is unnecessary when fork_turns is none")
        return fork, alerts
    try:
        turns = int(fork)
    except ValueError:
        alerts.append('fork_turns must be "none" or a positive integer')
        return fork, alerts
    if turns < 1 or turns > MAX_HISTORY_TURNS:
        alerts.append(f"fork_turns should be between 1 and {MAX_HISTORY_TURNS}")
    if args.history_exception is None:
        alerts.append("bounded history lacks a written --history-exception")
    return fork, alerts


def _reservation(args: argparse.Namespace) -> tuple[Reservation, list[str]]:
    kind = args.kind
    review_kinds = {"review", "review-refresh"}
    alerts: list[str] = []
    if args.units != 1 and kind != "live-eval":
        raise BudgetError("only live-eval accepts units greater than 1")
    if kind not in {"agent", *review_kinds} and args.agent_id is not None:
        raise BudgetError(f"{kind} does not accept --agent-id")
    if kind not in review_kinds and args.scope is not None:
        raise BudgetError(f"{kind} does not accept --scope")
    if kind != "live-eval" and args.static_check is not None:
        raise BudgetError(f"{kind} does not accept --static-check")
    agent = args.agent_id
    fork: str | None = None
    if kind in {"agent", *review_kinds}:
        agent = agent or args.reservation_id
        if args.agent_id is None:
            alerts.append("missing --agent-id; reservation id was used as the agent identity")
        fork, fork_alerts = _parse_fork_turns(args, kind in review_kinds)
        alerts.extend(fork_alerts)
    elif args.fork_turns is not None or args.history_exception is not None:
        raise BudgetError(f"{kind} does not accept fork history")
    scope = args.scope or ("unspecified" if kind in review_kinds else None)
    if kind in review_kinds and args.scope is None:
        alerts.append("missing --scope; review cannot be grouped with its correction loop")
    if kind == "live-eval" and args.static_check is None:
        alerts.append("live eval has no recorded static-contract check")
    value: Reservation = {"kind": kind, "units": args.units}
    for key, item in (
        ("agent_id", agent),
        ("fork_turns", fork),
        ("scope", scope),
        ("static_check", args.static_check),
        ("stage", args.stage),
        ("model", args.model),
        ("effort", args.effort),
        ("category", args.category),
        ("retry_of", args.retry_of),
        ("reason", args.reason),
        ("history_exception", args.history_exception),
        ("refresh_item_id", getattr(args, "refresh_item_id", None)),
        ("refresh_worktree", getattr(args, "refresh_worktree", None)),
        ("refresh_previous_attempt", getattr(args, "refresh_previous_attempt", None)),
        ("refresh_previous_tree", getattr(args, "refresh_previous_tree", None)),
        ("refresh_previous_base", getattr(args, "refresh_previous_base", None)),
        ("refresh_current_tree", getattr(args, "refresh_current_tree", None)),
        ("refresh_current_base", getattr(args, "refresh_current_base", None)),
    ):
        if item is not None:
            value[key] = item
    if alerts:
        value["alerts"] = alerts
    return value, alerts


def _reserve(state: dict[str, object], args: argparse.Namespace) -> tuple[bool, str]:
    if state.get("dispatch_frozen"):
        raise BudgetError("dispatch is frozen for rollback reconciliation")
    reservations = cast(dict[str, Reservation], state["reservations"])
    value, alerts = _reservation(args)
    existing = reservations.get(args.reservation_id)
    if existing is not None:
        existing_fields = dict(existing)
        existing_fields.pop("alerts", None)
        requested_fields = dict(value)
        requested_fields.pop("alerts", None)
        if existing_fields != requested_fields:
            raise BudgetError(
                f"reservation id already exists with different details: {args.reservation_id}"
            )
        return False, "already_reserved"
    limits = cast(Limits, state["limits"])
    usage = cast(Usage, state["usage"])
    agents = cast(dict[str, Agent], state["agents"])
    kind = args.kind
    agent = cast(str | None, value.get("agent_id"))
    if kind in {"agent", "review", "review-refresh"} and agent is not None and agent not in agents:
        if len(usage["agent_ids"]) >= limits["max_agent_threads"]:
            alerts.append("agent thread target exceeded")
        agents[agent] = {
            "fork_turns": cast(str, value["fork_turns"]),
            "history_exception": cast(str | None, value.get("history_exception")),
        }
        usage["agent_ids"].append(agent)
    if kind == "review":
        scope = cast(str, value["scope"])
        current = usage["review_rounds_by_scope"].get(scope, 0)
        if current + 1 > limits["max_review_rounds_per_scope"]:
            alerts.append(f"review round target exceeded for scope: {scope}")
        usage["review_rounds_by_scope"][scope] = current + 1
    elif kind == "live-eval":
        if usage["live_eval_calls"] + args.units > limits["max_live_eval_calls"]:
            alerts.append("live evaluation call target exceeded")
        usage["live_eval_calls"] += args.units
    elif kind == "fast-gate":
        if usage["fast_gates"] + 1 > limits["max_fast_gates"]:
            alerts.append("full fast gate target exceeded")
        usage["fast_gates"] += 1
    elif kind == "integration-check":
        if usage["integration_checks"] + 1 > limits["max_integration_checks"]:
            alerts.append("integration-check target exceeded")
        usage["integration_checks"] += 1
    if alerts:
        value["alerts"] = alerts
    reservations[args.reservation_id] = value
    if any("target exceeded" in alert for alert in alerts):
        trigger_id = "overage-" + hashlib.sha256(args.reservation_id.encode()).hexdigest()
        cast(dict[str, object], state["triggers"]).setdefault(
            trigger_id,
            {
                "trigger_id": trigger_id,
                "kind": "budget_overage",
                "reservation_id": args.reservation_id,
                "blocking": True,
                "created_at": _utc_now(),
            },
        )
    return True, "over_budget" if alerts else "recorded"


def _reserved_summary(state: dict[str, object]) -> dict[str, object]:
    limits = cast(Limits, state["limits"])
    usage = cast(Usage, state["usage"])
    return {
        "limits": limits,
        "remaining": {
            "agent_threads": max(0, limits["max_agent_threads"] - len(usage["agent_ids"])),
            "live_eval_calls": max(0, limits["max_live_eval_calls"] - usage["live_eval_calls"]),
            "fast_gates": max(0, limits["max_fast_gates"] - usage["fast_gates"]),
            "integration_checks": max(
                0, limits["max_integration_checks"] - usage["integration_checks"]
            ),
        },
        "over_budget": {
            "agent_threads": max(0, len(usage["agent_ids"]) - limits["max_agent_threads"]),
            "live_eval_calls": max(0, usage["live_eval_calls"] - limits["max_live_eval_calls"]),
            "fast_gates": max(0, usage["fast_gates"] - limits["max_fast_gates"]),
            "integration_checks": max(
                0, usage["integration_checks"] - limits["max_integration_checks"]
            ),
            "review_rounds_by_scope": {
                scope: rounds - limits["max_review_rounds_per_scope"]
                for scope, rounds in usage["review_rounds_by_scope"].items()
                if rounds > limits["max_review_rounds_per_scope"]
            },
        },
        "usage": usage,
    }


def _model_budget_overages(state: dict[str, object]) -> dict[str, int]:
    """Return current overages for model-backed dispatch resources."""
    limits = cast(Limits, state["limits"])
    usage = cast(Usage, state["usage"])
    overages: dict[str, int] = {}
    agent_threads = len(usage["agent_ids"])
    if agent_threads > limits["max_agent_threads"]:
        overages["agent_threads"] = agent_threads - limits["max_agent_threads"]
    if usage["live_eval_calls"] > limits["max_live_eval_calls"]:
        overages["live_eval_calls"] = usage["live_eval_calls"] - limits["max_live_eval_calls"]
    review_rounds = {
        scope: rounds - limits["max_review_rounds_per_scope"]
        for scope, rounds in usage["review_rounds_by_scope"].items()
        if rounds > limits["max_review_rounds_per_scope"]
    }
    if review_rounds:
        overages["review_rounds"] = sum(review_rounds.values())
    return overages


def _assert_model_dispatch_within_limits(state: dict[str, object], kind: str) -> None:
    """Refuse model dispatch while any model ceiling is currently exceeded."""
    if kind not in {"agent", "review", "review-refresh", "live-eval"}:
        return
    overages = _model_budget_overages(state)
    if overages:
        details = ", ".join(f"{name}={amount}" for name, amount in sorted(overages.items()))
        raise BudgetError(
            "model dispatch exceeds current limits "
            f"({details}); explicit owner-approved amendment required"
        )


def _usage_from_reservations(reservations: Mapping[str, Reservation]) -> Usage:
    agents: list[str] = []
    live_eval = 0
    reviews: dict[str, int] = {}
    fast_gates = 0
    integration = 0
    for reservation in reservations.values():
        kind = reservation.get("kind")
        if (
            kind in {"agent", "review", "review-refresh"}
            and isinstance(reservation.get("agent_id"), str)
            and reservation["agent_id"] not in agents
        ):
            agents.append(cast(str, reservation["agent_id"]))
        units = _require_int(reservation.get("units"), "reservation units", 1)
        if kind == "live-eval":
            live_eval += units
        elif kind == "review":
            scope = cast(str, reservation.get("scope", "unspecified"))
            reviews[scope] = reviews.get(scope, 0) + 1
        elif kind == "fast-gate":
            fast_gates += 1
        elif kind == "integration-check":
            integration += 1
    return {
        "agent_ids": agents,
        "live_eval_calls": live_eval,
        "review_rounds_by_scope": reviews,
        "fast_gates": fast_gates,
        "integration_checks": integration,
    }


def _usage_semantically_equal(left: object, right: Usage) -> bool:
    if not isinstance(left, dict):
        return False
    return (
        isinstance(left.get("agent_ids"), list)
        and all(isinstance(item, str) for item in left["agent_ids"])
        and set(left["agent_ids"]) == set(right["agent_ids"])
        and left.get("live_eval_calls") == right["live_eval_calls"]
        and left.get("review_rounds_by_scope") == right["review_rounds_by_scope"]
        and left.get("fast_gates") == right["fast_gates"]
        and left.get("integration_checks") == right["integration_checks"]
    )


def _v1_reservation_projection(value: object) -> dict[str, object]:
    """Project a v2 reservation to the exact historical v1 shape."""
    reservation = _require_mapping(value, "reservation")
    projected = {
        field: reservation[field] for field in V1_RESERVATION_FIELDS if field in reservation
    }
    if projected.get("kind") == "review-refresh":
        projected["kind"] = "agent"
        projected.pop("scope", None)
    return projected


def _v1_projection(state: dict[str, object]) -> dict[str, object]:
    """Return only fields understood by the committed v1 writer."""
    reservations = cast(dict[str, object], state["reservations"])
    return {
        "schema_version": LEGACY_SCHEMA_VERSION,
        "limits": copy.deepcopy(state["limits"]),
        "agents": copy.deepcopy(state["agents"]),
        "usage": copy.deepcopy(state["usage"]),
        "reservations": {
            rid: _v1_reservation_projection(value) for rid, value in reservations.items()
        },
        "amendments": copy.deepcopy(state["amendments"]),
    }


def _retry_chain_contains(
    attempt: dict[str, object], target_attempt_id: object, attempts: dict[str, Attempt]
) -> bool:
    parent = attempt.get("retry_of")
    seen: set[str] = set()
    while isinstance(parent, str):
        if parent in seen:
            return False
        if parent == target_attempt_id:
            return True
        seen.add(parent)
        parent_attempt = attempts.get(parent)
        if parent_attempt is None:
            return False
        parent = parent_attempt.get("retry_of")
    return False


def _validate_refresh_start(args: argparse.Namespace, reservation: Reservation) -> None:
    """Revalidate the exact approval and candidate bound to a free refresh."""
    required = (
        "refresh_item_id",
        "refresh_worktree",
        "refresh_previous_attempt",
        "refresh_previous_tree",
        "refresh_previous_base",
        "refresh_current_tree",
        "refresh_current_base",
    )
    provenance = {field: reservation.get(field) for field in required}
    if not all(isinstance(value, str) and value for value in provenance.values()):
        raise BudgetError("review refresh reservation lacks verified provenance")
    state_path = getattr(args, "resolved_state_path", None)
    if not isinstance(state_path, Path):
        raise BudgetError("review refresh cannot resolve canonical state")
    latest = state_path.with_suffix(".reviews") / "latest.json"
    if latest.is_symlink() or not latest.is_file():
        raise BudgetError("review refresh approval history is unavailable")
    try:
        previous: object = json.loads(_read_bounded_bytes(latest, "latest review verdict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError("latest review verdict is invalid") from exc
    context = previous.get("review_context") if isinstance(previous, dict) else None
    if (
        not isinstance(previous, dict)
        or previous.get("verdict") != "APPROVED"
        or not isinstance(context, dict)
        or context.get("item_id") != provenance["refresh_item_id"]
        or context.get("attempt_id") != provenance["refresh_previous_attempt"]
        or context.get("tree") != provenance["refresh_previous_tree"]
        or context.get("diff_base") != provenance["refresh_previous_base"]
    ):
        raise BudgetError("review refresh approval changed after reservation")
    try:
        worktree = Path(cast(str, provenance["refresh_worktree"])).resolve(strict=True)
    except OSError as exc:
        raise BudgetError("review refresh worktree is no longer valid") from exc
    repository_root = Path(_review_git(worktree, "rev-parse", "--show-toplevel")).resolve()
    if repository_root != worktree:
        raise BudgetError("review refresh worktree is no longer valid")
    if (
        _review_git(worktree, "diff", "--name-only")
        or _review_git(worktree, "ls-files", "--others", "--exclude-standard")
        or _review_git(worktree, "ls-files", "-u")
    ):
        raise BudgetError("review refresh candidate changed after reservation")
    current_tree = _review_git(worktree, "write-tree")
    if current_tree != provenance["refresh_current_tree"]:
        raise BudgetError("review refresh candidate changed after reservation")
    current_base = _review_git(
        worktree,
        "rev-parse",
        "--verify",
        f"{provenance['refresh_current_base']}^{{commit}}",
    )
    if current_base != provenance["refresh_current_base"]:
        raise BudgetError("review refresh base changed after reservation")


def _start(state: dict[str, object], args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    if state.get("dispatch_frozen"):
        raise BudgetError("dispatch is frozen for rollback reconciliation")
    attempts = cast(dict[str, Attempt], state["attempts"])
    reservations = cast(dict[str, Reservation], state["reservations"])
    reservation = reservations.get(args.reservation_id)
    if reservation is None:
        raise BudgetError("reservation does not exist")
    if reservation.get("kind") == "review-refresh":
        _validate_refresh_start(args, reservation)
    requested_category = args.category or reservation.get("category")
    if requested_category is not None and requested_category not in CATEGORY_VALUES:
        raise BudgetError("category is not in the closed orchestration category set")
    attempt: Attempt = {
        "attempt_id": args.attempt_id,
        "reservation_id": args.reservation_id,
        "status": "running",
        "started_at": _timestamp(args.started_at, "started_at"),
        "usage_source": "unavailable",
        "usage_channels": [],
    }
    for key, value in (
        ("stage", args.stage or reservation.get("stage")),
        ("model", args.model or reservation.get("model")),
        ("effort", args.effort or reservation.get("effort")),
        ("category", args.category or reservation.get("category")),
        ("retry_of", args.retry_of or reservation.get("retry_of")),
        ("retry_diagnosis", args.retry_diagnosis),
    ):
        if value is not None:
            attempt[key] = value
    existing = attempts.get(args.attempt_id)
    if existing is not None:
        if args.started_at is None:
            comparable = dict(attempt)
            comparable.pop("started_at", None)
            prior = dict(existing)
            prior.pop("started_at", None)
            if comparable == prior:
                return existing, False
        if existing != attempt:
            raise BudgetError("attempt replay conflicts with existing record")
        return existing, False
    _assert_model_dispatch_within_limits(state, cast(str, reservation.get("kind")))
    parent = cast(str | None, attempt.get("retry_of"))
    seen: set[str] = set()
    while parent is not None:
        if parent in seen:
            raise BudgetError("retry chain contains a cycle")
        seen.add(parent)
        parent_attempt = attempts.get(parent)
        if parent_attempt is None:
            raise BudgetError("retry parent attempt does not exist")
        if parent_attempt.get("status") not in TERMINAL_STATUSES:
            raise BudgetError("retry chain contains a nonterminal attempt")
        parent = cast(str | None, parent_attempt.get("retry_of"))
    recovery = cast(dict[str, object], state["recoveries"]).get(args.attempt_id)
    if recovery is not None and cast(dict[str, object], recovery).get("consumed"):
        raise BudgetError("one-shot recovery authorization was already consumed")
    same = [item for item in attempts.values() if item.get("reservation_id") == args.reservation_id]
    kind = cast(str, reservation.get("kind"))
    if kind != "live-eval" and any(item.get("status") == "running" for item in same):
        raise BudgetError("reservation already has a running attempt")
    if kind in {"review", "review-refresh"} and same:
        retry_parent = attempts.get(cast(str, attempt.get("retry_of")))
        if (
            len(same) >= 3
            or retry_parent is None
            or retry_parent.get("reservation_id") != args.reservation_id
            or retry_parent.get("status") not in {"failed", "interrupted"}
            or retry_parent.get("result_code") == "review_succeeded"
            or any(item.get("retry_of") == retry_parent.get("attempt_id") for item in same)
            or args.retry_diagnosis is None
            or any(
                retry_parent.get(field) != attempt.get(field)
                for field in ("stage", "model", "effort")
            )
        ):
            raise BudgetError("review retry requires the latest no-verdict attempt and diagnosis")
    elif args.retry_diagnosis is not None:
        raise BudgetError("retry diagnosis applies only to a no-verdict review retry")
    elif kind != "agent" and len(same) >= _require_int(
        reservation.get("units"), "reservation units", 1
    ):
        raise BudgetError("reservation cardinality is exhausted")
    execution_signature = [
        reservation.get("kind"),
        attempt.get("stage"),
        attempt.get("model"),
    ]
    matching_triggers = [
        trigger
        for trigger in cast(dict[str, object], state["triggers"]).values()
        if isinstance(trigger, dict)
        and trigger.get("blocking")
        and trigger.get("kind") == "repeated_failure"
        and isinstance(trigger.get("signature"), list)
        and cast(list[object], trigger["signature"])[:3] == execution_signature
    ]
    overage_triggers = [
        trigger
        for trigger in cast(dict[str, object], state["triggers"]).values()
        if isinstance(trigger, dict)
        and trigger.get("blocking")
        and trigger.get("kind") == "budget_overage"
        and trigger.get("reservation_id") == args.reservation_id
    ]
    applicable_triggers = matching_triggers + overage_triggers
    authorization = cast(dict[str, object], state["recoveries"]).get(args.attempt_id)
    authorized_triggers: list[dict[str, object]] = []
    if applicable_triggers:
        if (
            not isinstance(authorization, dict)
            or authorization.get("next_attempt_id") != args.attempt_id
        ):
            raise BudgetError("dispatch requires authorization for its exact blocking trigger")
        applicable_ids = {
            cast(dict[str, object], trigger).get("trigger_id") for trigger in applicable_triggers
        }
        recovery_trigger_ids = authorization.get("trigger_ids")
        if recovery_trigger_ids is None:
            recovery_trigger_ids = [authorization.get("trigger_id")]
        if (
            not isinstance(recovery_trigger_ids, list)
            or set(recovery_trigger_ids) != applicable_ids
        ):
            raise BudgetError("recovery must cover every applicable blocking trigger")
        authorized_triggers = [
            trigger
            for trigger in applicable_triggers
            if trigger.get("trigger_id") in recovery_trigger_ids
        ]
        repeated_triggers = [
            trigger for trigger in authorized_triggers if trigger.get("kind") == "repeated_failure"
        ]
        # Batched calls may finish as sibling attempts and create several same-signature
        # triggers. A single-parent retry chain cannot contain every sibling. Explicit recovery
        # must still cover every trigger above; lineage to any failed sibling proves that the
        # new attempt is a retry of the blocked batch rather than an unrelated dispatch.
        if repeated_triggers and not any(
            _retry_chain_contains(attempt, trigger.get("attempt_id"), attempts)
            for trigger in repeated_triggers
        ):
            raise BudgetError("recovery does not authorize this retry lineage")
    elif authorization is not None:
        raise BudgetError("recovery trigger does not apply to this attempt")
    if len(attempts) >= MAX_ATTEMPTS:
        raise BudgetError("attempt bound exceeded")
    if recovery is not None:
        cast(dict[str, object], recovery)["consumed"] = True
        for trigger in authorized_triggers:
            # Consuming a targeted recovery closes exactly the trigger it authorizes.
            # A later failure can therefore create a distinct fresh trigger.
            trigger.update(
                {
                    "blocking": False,
                    "resolved_at": attempt["started_at"],
                    "resolved_by": args.attempt_id,
                }
            )
    attempts[args.attempt_id] = attempt
    return attempt, True


def _token(value: int | None) -> Token:
    return {"value": value, "coverage": "measured" if value is not None else "unavailable"}


def _measured_token_value(token: Token, label: str) -> int:
    if token["coverage"] != "measured" or token["value"] is None:
        raise BudgetError(f"{label} must be measured")
    return token["value"]


def _attempt_token_is_measured(attempt: Attempt, token_name: str) -> bool:
    tokens = attempt.get("tokens")
    if not isinstance(tokens, dict):
        return False
    token = tokens.get(token_name)
    return isinstance(token, dict) and token.get("coverage") == "measured"


def _finish(state: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    if state.get("dispatch_frozen"):
        raise BudgetError("dispatch is frozen for rollback reconciliation")
    attempts = cast(dict[str, Attempt], state["attempts"])
    attempt = attempts.get(args.attempt_id)
    if attempt is None:
        raise BudgetError("attempt does not exist")
    if attempt.get("status") in TERMINAL_STATUSES:
        if attempt.get("status") != args.status or attempt.get("result_code") != args.result_code:
            raise BudgetError("terminal attempt replay conflicts with existing record")
        normalized_ended = (
            _timestamp(args.ended_at, "ended_at") if args.ended_at is not None else None
        )
        if normalized_ended is not None and attempt.get("ended_at") != normalized_ended:
            raise BudgetError("terminal attempt replay conflicts with existing record")
        if args.elapsed_ms is not None and attempt.get("elapsed_ms") != args.elapsed_ms:
            raise BudgetError("terminal attempt replay conflicts with existing record")
        existing_tokens = cast(dict[str, object], attempt.get("tokens", {}))
        for argument, field in (
            (args.input_tokens, "input_tokens"),
            (args.cached_input_tokens, "cached_input_tokens"),
            (args.uncached_input_tokens, "uncached_input_tokens"),
            (args.output_tokens, "output_tokens"),
        ):
            if argument is not None and existing_tokens.get(field) != _token(argument):
                raise BudgetError("terminal attempt replay conflicts with existing record")
        if (
            args.integrity_code is not None
            and attempt.get("usage_integrity") != args.integrity_code
        ):
            raise BudgetError("terminal attempt replay conflicts with existing record")
        if (
            getattr(args, "usage_source", None) is not None
            and attempt.get("usage_source") != args.usage_source
        ):
            raise BudgetError("terminal attempt replay conflicts with existing record")
        if getattr(args, "usage_channel", None) and attempt.get("usage_channels", []) != list(
            args.usage_channel
        ):
            raise BudgetError("terminal attempt replay conflicts with existing record")
        return attempt
    update = dict(attempt)
    update.update(
        {
            "status": args.status,
            "ended_at": _timestamp(args.ended_at, "ended_at"),
            "result_code": args.result_code,
        }
    )
    started_time = datetime.fromisoformat(cast(str, attempt["started_at"]).replace("Z", "+00:00"))
    ended_time = datetime.fromisoformat(cast(str, update["ended_at"]).replace("Z", "+00:00"))
    if ended_time < started_time:
        raise BudgetError("ended_at cannot precede started_at")
    if args.elapsed_ms is not None:
        update["elapsed_ms"] = args.elapsed_ms
    else:
        elapsed = round((ended_time - started_time).total_seconds() * 1000)
        update["elapsed_ms"] = elapsed
    tokens: dict[str, Token] = {
        "input_tokens": _token(args.input_tokens),
        "cached_input_tokens": _token(args.cached_input_tokens),
        "uncached_input_tokens": _token(args.uncached_input_tokens),
        "output_tokens": _token(args.output_tokens),
    }
    if (
        tokens["input_tokens"]["coverage"] == "measured"
        and tokens["cached_input_tokens"]["coverage"] == "measured"
    ):
        cached_input = _measured_token_value(tokens["cached_input_tokens"], "cached input")
        input_tokens = _measured_token_value(tokens["input_tokens"], "input")
        if cached_input > input_tokens:
            raise BudgetError("cached input cannot exceed input")
        if tokens["uncached_input_tokens"]["coverage"] == "unavailable":
            tokens["uncached_input_tokens"] = _token(input_tokens - cached_input)
    elif tokens["uncached_input_tokens"]["coverage"] == "measured":
        raise BudgetError("uncached input requires measured input and cached input")
    if (
        tokens["input_tokens"]["coverage"] == "measured"
        and tokens["cached_input_tokens"]["coverage"] == "measured"
        and tokens["uncached_input_tokens"]["coverage"] == "measured"
        and _measured_token_value(tokens["uncached_input_tokens"], "uncached input")
        != _measured_token_value(tokens["input_tokens"], "input")
        - _measured_token_value(tokens["cached_input_tokens"], "cached input")
    ):
        raise BudgetError("uncached input must equal input minus cached input")
    update["tokens"] = tokens
    if args.integrity_code is not None:
        update["usage_integrity"] = args.integrity_code
    update["usage_source"] = getattr(args, "usage_source", None) or "unavailable"
    update["usage_channels"] = list(getattr(args, "usage_channel", None) or [])
    if attempt.get("status") in TERMINAL_STATUSES and attempt != update:
        raise BudgetError("terminal attempt replay conflicts with existing record")
    attempts[args.attempt_id] = update
    if args.status == "failed":
        reservations = cast(dict[str, Reservation], state["reservations"])
        reservation = reservations[cast(str, attempt["reservation_id"])]
        signature = [
            reservation.get("kind"),
            attempt.get("stage"),
            attempt.get("model"),
            args.result_code,
        ]
        terminal_items = sorted(
            (item for item in attempts.values() if item.get("status") in TERMINAL_STATUSES),
            key=lambda item: (
                _instant(item.get("ended_at"), "attempt ended_at"),
                cast(str, item["attempt_id"]),
            ),
        )
        terminal_signatures = [
            (
                item,
                [
                    reservations[cast(str, item["reservation_id"])].get("kind"),
                    item.get("stage"),
                    item.get("model"),
                    item.get("result_code"),
                ],
            )
            for item in terminal_items
        ]
        if (
            len(terminal_signatures) >= 2
            and terminal_signatures[-1][0].get("status") == "failed"
            and terminal_signatures[-2][0].get("status") == "failed"
            and terminal_signatures[-1][1] == terminal_signatures[-2][1] == signature
        ):
            trigger_id = (
                "failure-"
                + hashlib.sha256(
                    json.dumps(
                        {"signature": signature, "attempt_id": attempt["attempt_id"]},
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
            )
            cast(dict[str, object], state["triggers"]).setdefault(
                trigger_id,
                {
                    "trigger_id": trigger_id,
                    "kind": "repeated_failure",
                    "signature": signature,
                    "attempt_id": attempt["attempt_id"],
                    "blocking": True,
                    "created_at": update["ended_at"],
                },
            )
    return update


def _reconcile(state: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    if state.get("dispatch_frozen"):
        raise BudgetError("dispatch is frozen for rollback reconciliation")
    attempt = cast(dict[str, Attempt], state["attempts"]).get(args.attempt_id)
    if attempt is None:
        raise BudgetError("attempt does not exist")
    if attempt.get("status") == "interrupted":
        if args.result_code is not None and attempt.get("result_code") != args.result_code:
            raise BudgetError("reconcile replay conflicts with existing result code")
        if args.ended_at is not None and attempt.get("ended_at") != _timestamp(
            args.ended_at, "ended_at"
        ):
            raise BudgetError("reconcile replay conflicts with existing ended_at")
        return attempt
    if attempt.get("status") in TERMINAL_STATUSES:
        raise BudgetError("only running attempts can be reconciled")
    ended_at = _timestamp(args.ended_at, "ended_at")
    started_at = datetime.fromisoformat(cast(str, attempt["started_at"]).replace("Z", "+00:00"))
    ended_time = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    if ended_time < started_at:
        raise BudgetError("ended_at cannot precede started_at")
    attempt.update(
        {
            "status": "interrupted",
            "ended_at": ended_at,
            "elapsed_ms": round((ended_time - started_at).total_seconds() * 1000),
            "result_code": args.result_code or "interrupted-reconciled",
            "tokens": {
                "input_tokens": _token(None),
                "cached_input_tokens": _token(None),
                "uncached_input_tokens": _token(None),
                "output_tokens": _token(None),
            },
        }
    )
    return attempt


def _recover(state: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    if state.get("dispatch_frozen"):
        raise BudgetError("dispatch is frozen for rollback reconciliation")
    trigger = cast(dict[str, object], state["triggers"]).get(args.trigger_id)
    if not isinstance(trigger, dict) or not trigger.get("blocking"):
        raise BudgetError("recovery trigger is not blocking")
    if args.targeted_check_result != "passed":
        raise BudgetError("targeted preflight must pass")
    reservation_id = trigger.get("reservation_id")
    reservation = (
        cast(dict[str, Reservation], state["reservations"]).get(reservation_id)
        if isinstance(reservation_id, str)
        else None
    )
    if (
        trigger.get("kind") == "budget_overage"
        and isinstance(reservation, dict)
        and reservation.get("kind") in {"agent", "review", "review-refresh", "live-eval"}
        and _model_budget_overages(state)
    ):
        raise BudgetError(
            "model budget recovery cannot authorize over-limit dispatch; "
            "explicit owner-approved amendment required"
        )
    recoveries = cast(dict[str, object], state["recoveries"])
    existing = recoveries.get(args.next_attempt_id)
    record = {
        "trigger_id": args.trigger_id,
        "trigger_ids": [args.trigger_id],
        "next_attempt_id": args.next_attempt_id,
        "targeted_check_id": args.targeted_check_id,
        "targeted_check_result": "passed",
        "next_action": args.next_action,
        "consumed": False,
        "created_at": _utc_now(),
    }
    if existing is not None:
        existing_record = cast(dict[str, object], existing)
        existing_trigger_ids = existing_record.get("trigger_ids")
        if existing_trigger_ids is None:
            existing_trigger_ids = [existing_record.get("trigger_id")]
            existing_record["trigger_ids"] = existing_trigger_ids
        if not isinstance(existing_trigger_ids, list):
            raise BudgetError("recovery trigger coverage is invalid")
        if existing_record.get("consumed"):
            raise BudgetError("one-shot recovery authorization was already consumed")
        if args.trigger_id not in existing_trigger_ids:
            existing_trigger_ids.append(args.trigger_id)
        existing_without_time = dict(existing_record)
        existing_without_time.pop("created_at", None)
        expected_without_time = dict(record)
        expected_without_time["trigger_id"] = existing_record["trigger_id"]
        expected_without_time["trigger_ids"] = existing_trigger_ids
        expected_without_time.pop("created_at", None)
        if existing_without_time != expected_without_time:
            raise BudgetError("recovery replay conflicts with existing record")
        return existing_record
    recoveries[args.next_attempt_id] = record
    return record


def _report(state: dict[str, object]) -> dict[str, object]:
    attempts = cast(dict[str, Attempt], state["attempts"])
    reservations = cast(dict[str, Reservation], state["reservations"])
    groups: dict[tuple[str, str], dict[str, object]] = {}
    intervals: list[tuple[float, float]] = []
    category_intervals: dict[str, list[tuple[float, float]]] = {}
    for attempt in attempts.values():
        stage = cast(str, attempt.get("stage", "unknown"))
        model = cast(str, attempt.get("model", "unknown"))
        key = (stage, model)
        group = groups.setdefault(
            key, {"stage": stage, "model": model, "attempts": 0, "terminal": 0, "tokens": {}}
        )
        group["attempts"] = cast(int, group["attempts"]) + 1
        if attempt.get("status") in TERMINAL_STATUSES:
            group["terminal"] = cast(int, group["terminal"]) + 1
        if attempt.get("started_at") and attempt.get("ended_at"):
            start = datetime.fromisoformat(
                cast(str, attempt["started_at"]).replace("Z", "+00:00")
            ).timestamp()
            end = datetime.fromisoformat(
                cast(str, attempt["ended_at"]).replace("Z", "+00:00")
            ).timestamp()
            stop = max(start, end)
            intervals.append((start, stop))
            category = cast(str, attempt.get("category", "unknown"))
            category_intervals.setdefault(category, []).append((start, stop))
        for name, token in cast(dict[str, Token], attempt.get("tokens", {})).items():
            if token["coverage"] == "measured":
                group_tokens = cast(dict[str, int], group["tokens"])
                group_tokens[name] = group_tokens.get(name, 0) + _measured_token_value(token, name)
                cast(list[str], group.setdefault("measured_fields", [])).append(name)
        source = attempt.get("usage_source", "unavailable")
        sources = cast(list[str], group.setdefault("usage_sources", []))
        if isinstance(source, str) and source not in sources:
            sources.append(source)

    def union_duration(values: list[tuple[float, float]]) -> float:
        total = 0.0
        end = -1.0
        for start, stop in sorted(values):
            total += stop - start if start > end else max(0.0, stop - end)
            end = max(end, stop)
        return total

    wall = union_duration(intervals)
    events: dict[float, int] = {}
    for start, stop in intervals:
        if stop <= start:
            continue
        events[start] = events.get(start, 0) + 1
        events[stop] = events.get(stop, 0) - 1
    overlap = 0.0
    active = 0
    previous_time: float | None = None
    for moment in sorted(events):
        if previous_time is not None and active >= 2:
            overlap += moment - previous_time
        active += events[moment]
        previous_time = moment
    category_durations = {
        category: union_duration(values) for category, values in category_intervals.items()
    }
    reserved_without_attempt = [
        rid
        for rid in reservations
        if not any(item.get("reservation_id") == rid for item in attempts.values())
    ]
    eligible = len(attempts)
    token_coverage: dict[str, dict[str, object]] = {}
    for token_name in (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
    ):
        measured = sum(
            1 for attempt in attempts.values() if _attempt_token_is_measured(attempt, token_name)
        )
        token_coverage[token_name] = {
            "measured_attempts": measured,
            "eligible_attempts": eligible,
            "coverage": "complete"
            if eligible and measured == eligible
            else "partial"
            if measured
            else "none",
        }
    model_coverage = "none"
    if any(cast(int, item["measured_attempts"]) > 0 for item in token_coverage.values()):
        model_coverage = (
            "complete"
            if all(item["coverage"] == "complete" for item in token_coverage.values())
            else "partial"
        )

    def projection(item: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
        return {field: item[field] for field in fields if field in item}

    for (stage, model), group in groups.items():
        members = [
            attempt
            for attempt in attempts.values()
            if attempt.get("stage", "unknown") == stage and attempt.get("model", "unknown") == model
        ]
        coverage: dict[str, dict[str, object]] = {}
        for token_name in TOKEN_FIELD_ORDER:
            measured = sum(
                1 for attempt in members if _attempt_token_is_measured(attempt, token_name)
            )
            eligible_group = len(members)
            coverage[token_name] = {
                "measured_attempts": measured,
                "eligible_attempts": eligible_group,
                "coverage": "complete"
                if eligible_group and measured == eligible_group
                else "partial"
                if measured
                else "none",
            }
        group["coverage"] = coverage
        if "measured_fields" in group:
            group["measured_fields"] = sorted(set(cast(list[str], group["measured_fields"])))
        group["usage_sources"] = sorted(set(cast(list[str], group["usage_sources"])))
        group["usage_channels"] = sorted(
            {
                channel
                for attempt in members
                for channel in cast(list[object], attempt.get("usage_channels", []))
                if isinstance(channel, str)
            }
        )

    trigger_fields = (
        "trigger_id",
        "kind",
        "reservation_id",
        "attempt_id",
        "signature",
        "blocking",
        "created_at",
        "resolved_at",
        "resolved_by",
    )
    recovery_fields = (
        "trigger_id",
        "trigger_ids",
        "next_attempt_id",
        "targeted_check_id",
        "targeted_check_result",
        "next_action",
        "consumed",
        "created_at",
    )
    attempt_fields = (
        "attempt_id",
        "reservation_id",
        "status",
        "started_at",
        "ended_at",
        "elapsed_ms",
        "result_code",
        "stage",
        "model",
        "effort",
        "category",
        "retry_of",
        "retry_diagnosis",
        "usage_integrity",
        "usage_source",
        "usage_channels",
    )
    observation_fields = (
        "kind",
        "blocking",
        "source",
        "base_hash",
        "projection_hash",
        "coverage",
    )
    channel_coverage = {
        channel: {
            "measured_attempts": sum(
                1
                for attempt in attempts.values()
                if channel in cast(list[object], attempt.get("usage_channels", []))
            ),
            "eligible_attempts": eligible,
            "coverage": "complete"
            if eligible
            and all(
                channel in cast(list[object], attempt.get("usage_channels", []))
                for attempt in attempts.values()
            )
            else "partial"
            if any(
                channel in cast(list[object], attempt.get("usage_channels", []))
                for attempt in attempts.values()
            )
            else "unavailable",
        }
        for channel in ("app", "native_agent", "permission_reviewer")
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "revision": state.get("revision", 0),
        "groups": list(groups.values()),
        "reserved_without_attempt": reserved_without_attempt,
        "attempt_count": len(attempts),
        "wall_time_ms": round(wall * 1000),
        "activity_time_ms_by_category": {
            category: round(duration * 1000) for category, duration in category_durations.items()
        },
        "overlap_time_ms": round(overlap * 1000),
        "triggers": [
            projection(cast(dict[str, object], item), trigger_fields)
            for item in cast(dict[str, object], state["triggers"]).values()
        ],
        "recoveries": [
            projection(cast(dict[str, object], item), recovery_fields)
            for item in cast(dict[str, object], state["recoveries"]).values()
        ],
        "attempts": [projection(item, attempt_fields) for item in attempts.values()],
        "observations": [
            projection(cast(dict[str, object], item), observation_fields)
            for item in cast(list[object], state["observations"])
            if isinstance(item, dict)
        ],
        "coverage": {
            "model_usage": model_coverage,
            "tokens": token_coverage,
            "permission_reviewer": "unavailable",
            "channels": channel_coverage,
        },
    }
    if (
        len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode())
        > MAX_REPORT_BYTES
    ):
        raise BudgetError("serialized report exceeds bound")
    return report


def _state_arg(args: argparse.Namespace) -> Path:
    if args.state is not None:
        state_path = cast(Path, args.state)
        if not state_path.is_absolute():
            raise BudgetError("--state must be an absolute path")
        return state_path
    if getattr(args, "state_root", None) is not None:
        if args.item_id is None:
            raise BudgetError("--state-root requires --item-id")
        if not args.item_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in args.item_id
        ):
            raise BudgetError("item id contains unsafe path characters")
        state_root = cast(Path, args.state_root)
        if state_root.is_symlink():
            raise BudgetError("state root cannot be a symlink")
        root = state_root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root / f"{args.item_id}.json"
    if args.worktree is None or args.item_id is None:
        raise BudgetError("provide --state or --worktree with --item-id")
    return canonical_state_path(cast(Path, args.worktree), cast(str, args.item_id))


def _init(args: argparse.Namespace) -> int:
    path = _state_arg(args)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = _with_lock(path)
    try:
        if path.exists():
            raise BudgetError(f"budget state already exists: {path}")
        legacy_source = None
        if args.worktree is not None and args.item_id is not None:
            candidate = args.worktree / "docs" / "work" / args.item_id / "orchestration-budget.json"
            try:
                relative = candidate.relative_to(args.worktree).as_posix()
            except ValueError as exc:
                raise BudgetError("legacy budget source is outside the worktree") from exc
            committed_bytes = _committed_blob(args.worktree, relative)
            if committed_bytes is not None:
                if candidate.is_symlink() or not candidate.is_file():
                    raise BudgetError("committed legacy budget source is missing or not regular")
                if (
                    _read_bounded_bytes(candidate, "committed legacy budget source")
                    != committed_bytes
                ):
                    raise BudgetError("legacy budget source must match the committed HEAD blob")
                legacy_source = candidate
            elif candidate.exists():
                raise BudgetError("legacy budget source is not committed at HEAD")
        if legacy_source is not None:
            if any(getattr(args, field) is not None for field in LIMIT_FIELDS):
                raise BudgetError("init limit overrides are forbidden when importing legacy source")
            try:
                raw = json.loads(
                    _read_bounded_bytes(legacy_source, "committed legacy budget source")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BudgetError("committed legacy budget source is not valid JSON") from exc
            if not isinstance(raw, dict) or raw.get("schema_version") != LEGACY_SCHEMA_VERSION:
                raise BudgetError("committed budget source is not schema v1")
            state = _legacy_to_v2(raw, legacy_source)
        else:
            state = _base_state()
        for field in LIMIT_FIELDS:
            value = getattr(args, field)
            if value is not None:
                cast(Limits, state["limits"])[field] = value
        _write_state(path, state)
    finally:
        _release_lock(lock)
    print(
        json.dumps(
            {"status": "initialized", "state": str(path), **_reserved_summary(state)},
            sort_keys=True,
        )
    )
    return 0


def _reserve_command(args: argparse.Namespace) -> int:
    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        state = _load_state(path)
        changed, status = _reserve(state, args)
        if changed:
            _write_state(path, state)
        output = {
            "alerts": cast(dict[str, Reservation], state["reservations"])[args.reservation_id].get(
                "alerts", []
            ),
            "reservation_id": args.reservation_id,
            "status": status,
            **_reserved_summary(state),
        }
    finally:
        _release_lock(lock)
    print(json.dumps(output, sort_keys=True))
    return 0


def _review_git(worktree: Path, *arguments: str) -> str:
    if GIT_BINARY is None:
        raise BudgetError("git executable was not found")
    try:
        result = subprocess.run(  # noqa: S603 -- fixed git executable and argument vector.
            [GIT_BINARY, "-C", str(worktree), *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BudgetError("cannot verify review refresh Git evidence") from exc
    if result.returncode != 0:
        raise BudgetError("review refresh Git evidence is invalid")
    return result.stdout.rstrip("\n")


def _reserve_review_refresh(args: argparse.Namespace) -> int:
    """Reserve a free refresh only from runner-owned approval and Git snapshots."""
    state_path = _state_arg(args)
    worktree = args.review_worktree.resolve(strict=True)
    repository_root = Path(_review_git(worktree, "rev-parse", "--show-toplevel")).resolve()
    if repository_root != worktree:
        raise BudgetError("review refresh worktree must be the repository root")
    latest = state_path.with_suffix(".reviews") / "latest.json"
    if latest.is_symlink() or not latest.is_file():
        raise BudgetError("review refresh requires the latest runner-owned verdict")
    try:
        previous: object = json.loads(_read_bounded_bytes(latest, "latest review verdict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError("latest review verdict is invalid") from exc
    if not isinstance(previous, dict) or previous.get("verdict") != "APPROVED":
        raise BudgetError("review refresh requires a prior APPROVED verdict")
    context = previous.get("review_context")
    if not isinstance(context, dict):
        raise BudgetError("review refresh verdict lacks snapshot evidence")
    previous_item = context.get("item_id")
    previous_attempt = context.get("attempt_id")
    if not isinstance(previous_item, str) or not previous_item:
        raise BudgetError("review refresh verdict has invalid item evidence")
    if not isinstance(previous_attempt, str) or not previous_attempt:
        raise BudgetError("review refresh verdict has invalid attempt evidence")
    if args.item_id is not None and context.get("item_id") != args.item_id:
        raise BudgetError("review refresh verdict belongs to another item")
    old_tree = context.get("tree")
    old_base = context.get("diff_base")
    if not isinstance(old_tree, str) or not isinstance(old_base, str):
        raise BudgetError("review refresh verdict has invalid snapshot evidence")
    old_tree = _review_git(worktree, "rev-parse", "--verify", f"{old_tree}^{{tree}}")
    old_base = _review_git(worktree, "rev-parse", "--verify", f"{old_base}^{{commit}}")
    current_base = _review_git(worktree, "rev-parse", "--verify", f"{args.current_base}^{{commit}}")
    if old_base == current_base:
        raise BudgetError("review refresh requires a changed Git base")
    if (
        _review_git(worktree, "diff", "--name-only")
        or _review_git(worktree, "ls-files", "--others", "--exclude-standard")
        or _review_git(worktree, "ls-files", "-u")
    ):
        raise BudgetError("review refresh requires one fully staged conflict-free candidate")
    current_tree = _review_git(worktree, "write-tree")
    paths = sorted(
        set(
            _review_git(
                worktree, "diff", "--no-renames", "--name-only", "-z", old_base, old_tree
            ).split("\0")
        )
        | set(
            _review_git(
                worktree,
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                current_base,
                current_tree,
            ).split("\0")
        )
        - {""}
    )
    literal_paths = [f":(literal){path}" for path in paths if path]
    correction = (
        _review_git(
            worktree,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            old_tree,
            current_tree,
            "--",
            *literal_paths,
        )
        if literal_paths
        else ""
    )
    if correction.strip():
        raise BudgetError("review refresh contains an item-path correction")
    args.kind = "review-refresh"
    args.units = 1
    args.agent_id = "codex-exec-code-reviewer"
    args.fork_turns = "none"
    args.history_exception = None
    args.scope = "code"
    args.static_check = None
    args.stage = None
    args.model = None
    args.effort = None
    args.category = None
    args.retry_of = None
    args.reason = "runner-verified-rebase-only"
    args.refresh_item_id = previous_item
    args.refresh_worktree = str(worktree)
    args.refresh_previous_attempt = previous_attempt
    args.refresh_previous_tree = old_tree
    args.refresh_previous_base = old_base
    args.refresh_current_tree = current_tree
    args.refresh_current_base = current_base
    return _reserve_command(args)


def _import_command(args: argparse.Namespace) -> int:
    source = args.source
    if source.is_symlink() or not source.is_file():
        raise BudgetError("legacy source must be a regular non-symlink file")
    source = source.resolve()
    try:
        source_bytes = _read_bounded_bytes(source, "legacy source")
        raw = json.loads(source_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError("legacy source is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise BudgetError("legacy source must be schema v1")
    if args.state is None and args.worktree is not None and args.item_id is not None:
        expected = args.worktree / "docs" / "work" / args.item_id / "orchestration-budget.json"
        if source != expected.resolve(strict=False):
            raise BudgetError("canonical import source must be the committed item budget blob")
        relative = expected.relative_to(args.worktree).as_posix()
        committed_bytes = _committed_blob(args.worktree, relative)
        if committed_bytes is None or source_bytes != committed_bytes:
            raise BudgetError("canonical import source must match the committed HEAD blob")
    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        digest = hashlib.sha256(source_bytes).hexdigest()
        if path.exists():
            state = _load_state(path)
            imports = cast(list[dict[str, object]], state.get("imports", []))
            if any(item.get("sha256") == digest for item in imports):
                print(
                    json.dumps({"status": "already_imported", "state": str(path)}, sort_keys=True)
                )
                return 0
            raise BudgetError("canonical state already exists with a conflicting legacy source")
        state = _legacy_to_v2(raw, source)
        _write_state(path, state)
    finally:
        _release_lock(lock)
    print(json.dumps({"status": "imported", "state": str(path)}, sort_keys=True))
    return 0


def _rollback_export(args: argparse.Namespace) -> int:
    state_path = _state_arg(args)
    output = args.output
    if output.is_symlink():
        raise BudgetError("rollback output cannot be a symlink")
    binding_output = output.with_suffix(output.suffix + ".binding.json")
    protected = (
        state_path,
        state_path.with_suffix(state_path.suffix + ".lock"),
        state_path.with_suffix(state_path.suffix + ".journal"),
    )
    for target in (output, binding_output):
        if target.is_symlink():
            raise BudgetError("rollback output cannot be a symlink")
        target_resolved = target.resolve(strict=False)
        for candidate in protected:
            if target_resolved == candidate.resolve(strict=False):
                raise BudgetError("rollback output aliases canonical budget storage")
            if target.exists() and candidate.exists():
                try:
                    if os.path.samefile(target, candidate):
                        raise BudgetError("rollback output aliases canonical budget storage")
                except OSError as exc:
                    raise BudgetError("cannot validate rollback output identity") from exc
    lock = _with_lock(state_path)
    try:
        state = _load_state(state_path)
        attempts = cast(dict[str, Attempt], state["attempts"])
        if any(attempt.get("status") == "running" for attempt in attempts.values()):
            raise BudgetError("rollback export requires running attempts to be reconciled first")
        if not state.get("dispatch_frozen"):
            state["dispatch_frozen"] = True
            _write_state(state_path, state)
            state = _load_state(state_path)
        projection = _v1_projection(state)
        output = output.resolve(strict=False)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        serialized = json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False
        ) as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
            temporary = Path(file.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
        _fsync_dir(output.parent)
        binding = {
            "schema_version": 1,
            "v2_revision": state.get("revision", 0),
            "v2_sha256": _sha_state(state),
            "projection_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "base_reservations": state["reservations"],
            "base_agents": state["agents"],
            "base_amendments": state["amendments"],
        }
        binding_serialized = (
            json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=binding_output.parent, delete=False
        ) as file:
            file.write(binding_serialized)
            file.flush()
            os.fsync(file.fileno())
            binding_temporary = Path(file.name)
        os.chmod(binding_temporary, 0o600)
        os.replace(binding_temporary, binding_output)
        _fsync_dir(binding_output.parent)
    finally:
        _release_lock(lock)
    print(
        json.dumps(
            {"status": "exported", "sha256": hashlib.sha256(serialized.encode()).hexdigest()},
            sort_keys=True,
        )
    )
    return 0


def _rollback_reconcile(args: argparse.Namespace) -> int:
    source = args.source
    if source.is_symlink() or not source.is_file():
        raise BudgetError("rollback source must be a regular non-symlink file")
    source = source.resolve()
    try:
        source_bytes = _read_bounded_bytes(source, "rollback source")
        raw = json.loads(source_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError("rollback source is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise BudgetError("rollback source must be schema v1")
    _legacy_to_v2(raw)
    projection_hash = hashlib.sha256(source_bytes).hexdigest()
    binding_path = source.with_suffix(source.suffix + ".binding.json")
    if binding_path.is_symlink() or not binding_path.is_file():
        raise BudgetError("rollback source is not bound to the canonical v2 export")
    try:
        binding = json.loads(_read_bounded_bytes(binding_path, "rollback binding"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudgetError("rollback binding is not valid JSON") from exc
    if not isinstance(binding, dict) or binding.get("v2_sha256") != args.base_hash:
        raise BudgetError("rollback source is not bound to the canonical v2 export")
    _reject_unknown(
        binding,
        {
            "schema_version",
            "v2_revision",
            "v2_sha256",
            "projection_sha256",
            "base_reservations",
            "base_agents",
            "base_amendments",
        },
        "rollback binding",
    )
    if binding.get("schema_version") != 1:
        raise BudgetError("rollback binding schema is unsupported")
    _require_int(binding.get("v2_revision"), "rollback binding revision")
    for field in ("v2_sha256", "projection_sha256"):
        _require_string(binding.get(field), f"rollback binding {field}")
    _require_mapping(binding.get("base_reservations"), "rollback binding reservations")
    _require_mapping(binding.get("base_agents"), "rollback binding agents")
    if not isinstance(binding.get("base_amendments"), list):
        raise BudgetError("rollback binding amendments are invalid")
    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        state = _load_state(path)
        if any(
            isinstance(observation, dict)
            and observation.get("kind") == "rollback_reconciled"
            and observation.get("base_hash") == args.base_hash
            and observation.get("projection_hash") == projection_hash
            for observation in cast(list[object], state["observations"])
        ):
            print(json.dumps({"status": "already_reconciled", "state": str(path)}, sort_keys=True))
            return 0
        if _sha_state(state) != args.base_hash:
            raise BudgetError("canonical state no longer matches the bound rollback base")
        if binding.get("v2_revision") != state.get("revision"):
            raise BudgetError("rollback source revision is not bound to the canonical base")
        source_limits = raw.get("limits")
        if not isinstance(source_limits, dict):
            raise BudgetError("rollback source limits are invalid")
        source_agents = cast(dict[str, object], raw.get("agents", {}))
        current_agents = cast(dict[str, object], state["agents"])
        for agent_id, value in current_agents.items():
            if source_agents.get(agent_id) != value:
                raise BudgetError("rollback reconciliation cannot mutate existing agents")
        source_usage = raw.get("usage")
        source_reservations = cast(
            dict[str, Reservation], _require_mapping(raw.get("reservations", {}), "reservations")
        )
        if not _usage_semantically_equal(
            source_usage, _usage_from_reservations(source_reservations)
        ):
            raise BudgetError("rollback source counters are not derivable")
        current_amendments = cast(list[object], state["amendments"])
        source_amendments = cast(list[object], raw.get("amendments", []))
        if source_amendments[: len(current_amendments)] != current_amendments:
            raise BudgetError("rollback reconciliation cannot mutate existing amendments")
        reconciled_limits = dict(cast(Limits, state["limits"]))
        for amendment in source_amendments[len(current_amendments) :]:
            if not isinstance(amendment, dict):
                raise BudgetError("rollback amendment is invalid")
            changes = amendment.get("changes")
            if not isinstance(changes, dict) or not changes:
                raise BudgetError("rollback amendment has no valid limit changes")
            if (
                not isinstance(amendment.get("owner_approval"), str)
                or not amendment["owner_approval"]
            ):
                raise BudgetError("rollback amendment lacks owner approval")
            for field, value in changes.items():
                if (
                    field not in LIMIT_FIELDS
                    or not isinstance(value, int)
                    or isinstance(value, bool)
                ):
                    raise BudgetError("rollback amendment contains an invalid limit")
                if value < 1:
                    raise BudgetError("rollback amendment limit must be positive")
                reconciled_limits[cast(LimitField, field)] = value
        if source_limits != reconciled_limits:
            raise BudgetError("rollback source limits do not match appended amendments")
        base_reservations = cast(dict[str, object], binding.get("base_reservations", {}))
        current_reservations = cast(dict[str, object], state["reservations"])
        for rid, value in current_reservations.items():
            if base_reservations.get(rid) != value:
                raise BudgetError("rollback reconciliation would mutate an existing reservation")
        for rid, value in base_reservations.items():
            source_value = source_reservations.get(rid)
            if not isinstance(source_value, dict):
                raise BudgetError("rollback reconciliation deleted a base reservation")
            expected_v1 = _v1_reservation_projection(value)
            if source_value != expected_v1:
                raise BudgetError("rollback reconciliation mutated a base reservation")
        current_reservations.update(
            {
                rid: value
                for rid, value in source_reservations.items()
                if rid not in current_reservations
            }
        )
        state["agents"] = source_agents
        state["limits"] = reconciled_limits
        state["amendments"] = source_amendments
        state["usage"] = source_usage
        state["dispatch_frozen"] = False
        state["observations"] = cast(list[object], state["observations"]) + [
            {
                "kind": "rollback_reconciled",
                "base_hash": args.base_hash,
                "projection_hash": projection_hash,
                "coverage": "unavailable",
            }
        ]
        _write_state(path, state)
    finally:
        _release_lock(lock)
    print(json.dumps({"status": "reconciled", "state": str(path)}, sort_keys=True))
    return 0


def _status(args: argparse.Namespace) -> int:
    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        state = _load_state(path)
    finally:
        _release_lock(lock)
    payload = json.dumps(
        {"state": str(path), **_reserved_summary(state), "report": _report(state)},
        sort_keys=True,
    )
    if len(payload.encode()) > MAX_REPORT_BYTES:
        raise BudgetError("serialized status report exceeds bound")
    print(payload)
    return 0


def _amend(args: argparse.Namespace) -> int:
    changes: dict[LimitField, int] = {}
    for field in LIMIT_FIELDS:
        value = getattr(args, field)
        if value is not None:
            changes[field] = _require_int(value, f"amend.{field}", 1)
    if not changes:
        raise BudgetError("amend requires at least one limit")

    def action(state: dict[str, object]) -> None:
        limits = cast(Limits, state["limits"])
        for field, value in changes.items():
            limits[field] = value
        cast(list[object], state["amendments"]).append(
            {"owner_approval": args.owner_approval, "changes": changes}
        )

    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        state = _load_state(path)
        if state.get("dispatch_frozen"):
            raise BudgetError("dispatch is frozen for rollback reconciliation")
        action(state)
        _write_state(path, state)
    finally:
        _release_lock(lock)
    print(json.dumps({"status": "amended", **_reserved_summary(state)}, sort_keys=True))
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--item-id", type=_bounded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def limits(command: argparse.ArgumentParser, defaults: bool) -> None:
        for field, default in _default_limits().items():
            command.add_argument(
                f"--{field.replace('_', '-')}",
                dest=field,
                type=_positive_int,
                default=default if defaults else None,
            )

    init = sub.add_parser("init")
    _common(init)
    # Keep imported v1 limits intact; absent flags use _base_state defaults.
    limits(init, False)
    init.set_defaults(handler=_init)
    reserve = sub.add_parser("reserve")
    _common(reserve)
    reserve.add_argument("--id", dest="reservation_id", required=True, type=_bounded)
    reserve.add_argument(
        "--kind",
        choices=(
            "agent",
            "review",
            "live-eval",
            "fast-gate",
            "integration-check",
        ),
        required=True,
    )
    reserve.add_argument("--units", type=_positive_int, default=1)
    reserve.add_argument("--agent-id", type=_bounded)
    reserve.add_argument("--fork-turns", type=_bounded)
    reserve.add_argument("--history-exception", type=_bounded)
    reserve.add_argument("--scope", type=_bounded)
    reserve.add_argument("--static-check", type=_bounded)
    reserve.add_argument("--stage", type=_bounded)
    reserve.add_argument("--model", type=_bounded)
    reserve.add_argument("--effort", type=_bounded)
    reserve.add_argument("--category", type=_bounded)
    reserve.add_argument("--retry-of", type=_bounded)
    reserve.add_argument("--reason", type=_bounded)
    reserve.set_defaults(handler=_reserve_command)
    refresh = sub.add_parser("reserve-review-refresh")
    _common(refresh)
    refresh.add_argument("--id", dest="reservation_id", required=True, type=_bounded)
    refresh.add_argument("--review-worktree", type=Path, required=True)
    refresh.add_argument("--current-base", required=True, type=_bounded)
    refresh.set_defaults(handler=_reserve_review_refresh)
    status = sub.add_parser("status")
    _common(status)
    status.set_defaults(handler=_status)
    amend = sub.add_parser("amend")
    _common(amend)
    amend.add_argument("--owner-approval", required=True, type=_bounded)
    limits(amend, False)
    amend.set_defaults(handler=_amend)
    imported = sub.add_parser("import")
    _common(imported)
    imported.add_argument("--source", type=Path, required=True)
    imported.set_defaults(handler=_import_command)
    start = sub.add_parser("start")
    _common(start)
    start.add_argument("--reservation-id", required=True, type=_bounded)
    start.add_argument("--attempt-id", required=True, type=_bounded)
    start.add_argument("--started-at", type=_bounded)
    start.add_argument("--stage", type=_bounded)
    start.add_argument("--model", type=_bounded)
    start.add_argument("--effort", type=_bounded)
    start.add_argument("--category", type=_bounded)
    start.add_argument("--retry-of", type=_bounded)
    start.add_argument("--retry-diagnosis", type=_bounded)
    start.set_defaults(handler=lambda a: _mutating_result(a, _start))
    finish = sub.add_parser("finish")
    _common(finish)
    finish.add_argument("--attempt-id", required=True, type=_bounded)
    finish.add_argument("--status", choices=tuple(TERMINAL_STATUSES), required=True)
    finish.add_argument("--result-code", required=True, type=_bounded)
    finish.add_argument("--ended-at", type=_bounded)
    finish.add_argument("--elapsed-ms", type=_non_negative_int)
    finish.add_argument("--input-tokens", type=_non_negative_int)
    finish.add_argument("--cached-input-tokens", type=_non_negative_int)
    finish.add_argument("--uncached-input-tokens", type=_non_negative_int)
    finish.add_argument("--output-tokens", type=_non_negative_int)
    finish.add_argument("--integrity-code", type=_bounded)
    finish.add_argument("--usage-source", choices=tuple(sorted(USAGE_SOURCES)))
    finish.add_argument("--usage-channel", action="append", choices=tuple(sorted(USAGE_CHANNELS)))
    finish.set_defaults(handler=lambda a: _mutating_result(a, _finish))
    reconcile = sub.add_parser("reconcile")
    _common(reconcile)
    reconcile.add_argument("--attempt-id", required=True, type=_bounded)
    reconcile.add_argument("--result-code", type=_bounded)
    reconcile.add_argument("--ended-at", type=_bounded)
    reconcile.set_defaults(handler=lambda a: _mutating_result(a, _reconcile))
    recover = sub.add_parser("recover")
    _common(recover)
    recover.add_argument("--trigger-id", required=True, type=_bounded)
    recover.add_argument("--next-attempt-id", required=True, type=_bounded)
    recover.add_argument("--targeted-check-id", required=True, type=_bounded)
    recover.add_argument("--targeted-check-result", choices=("passed", "failed"), required=True)
    recover.add_argument("--next-action", required=True, type=_bounded)
    recover.set_defaults(handler=lambda a: _mutating_result(a, _recover))
    report = sub.add_parser("report")
    _common(report)
    report.set_defaults(handler=lambda a: _report_command(a))
    path = sub.add_parser("state-path")
    path.add_argument("--worktree", type=Path, required=True)
    path.add_argument("--item-id", type=_bounded, required=True)
    path.set_defaults(handler=lambda a: print(canonical_state_path(a.worktree, a.item_id)) or 0)
    rollback = sub.add_parser("rollback-export")
    _common(rollback)
    rollback.add_argument("--output", type=Path, required=True)
    rollback.set_defaults(handler=_rollback_export)
    reconcile_rollback = sub.add_parser("rollback-reconcile")
    _common(reconcile_rollback)
    reconcile_rollback.add_argument("--source", type=Path, required=True)
    reconcile_rollback.add_argument("--base-hash", type=_bounded, required=True)
    reconcile_rollback.set_defaults(handler=_rollback_reconcile)
    return parser


def _mutating_result(
    args: argparse.Namespace, operation: Callable[[dict[str, object], argparse.Namespace], object]
) -> int:
    path = _state_arg(args)
    args.resolved_state_path = path
    lock = _with_lock(path)
    try:
        state = _load_state(path)
        before = _sha_state(state)
        result = operation(state, args)
        created: bool | None = None
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], bool):
            result, created = result
        if _sha_state(state) != before:
            _write_state(path, state)
    finally:
        _release_lock(lock)
    output: dict[str, object] = {"status": "recorded", "result": result}
    if created is not None:
        output["created"] = created
    print(json.dumps(output, sort_keys=True, default=str))
    return 0


def _report_command(args: argparse.Namespace) -> int:
    path = _state_arg(args)
    lock = _with_lock(path)
    try:
        state = _load_state(path)
    finally:
        _release_lock(lock)
    payload = json.dumps(_report(state), sort_keys=True)
    if len(payload.encode()) > MAX_REPORT_BYTES:
        raise BudgetError("serialized report exceeds bound")
    print(payload)
    return 0


def main() -> int:
    try:
        args = _parser().parse_args()
        return cast(Callable[[argparse.Namespace], int], args.handler)(args)
    except BudgetError as exc:
        print(f"budget refusal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
