#!/usr/bin/env python3
"""Persist bounded ordinary review telemetry and paired first-pass state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

COLLECTION_SCHEMA_VERSION = 2
COLLECTION_LIMIT = 10
MAX_JSON_BYTES = 256 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_NOTE_BYTES = 64 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_FINDINGS = 256
MAX_TEXT_FIELD = 4096
ITEM_ID_PATTERN = re.compile(r"\ANAD-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")
RUN_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
PHASES = frozenset({"initial", "followup", "synthesis"})
_VERDICT_PATTERN = re.compile(
    r"^\s*(APPROVED|REQUEST_CHANGES|NEEDS_REWORK|PROMOTION_READY)\s*$", re.MULTILINE
)
_SAFE_VERSION_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,127}\Z")
_FINDING_ID_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DEFAULT_ROOT = Path.home() / "Library" / "Application Support" / "Nadili" / "review-telemetry"
_COLLECTION_FILENAME = "ordinary-collection-v2.json"
_RUNS_DIRECTORY = "ordinary-runs-v2"
_NOTES_DIRECTORY = "ordinary-notes-v2"
_PAIRS_DIRECTORY = "ordinary-pairs-v2"
_PAIR_LOCKS_DIRECTORY = "ordinary-pair-locks-v2"
_DIGEST_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
_PAIR_SIDES = frozenset({"astra_medium", "astra_high"})


class TelemetryError(ValueError):
    """Raised when telemetry state is malformed or cannot be safely written."""


@dataclass(frozen=True, slots=True)
class TelemetryPaths:
    root: Path
    collection: Path
    runs: Path
    notes: Path
    pairs: Path
    pair_locks: Path
    lock: Path


@dataclass(frozen=True, slots=True)
class EventSummary:
    session_id: str | None
    usage: dict[str, int | None]
    usage_source: str | None


def activate(
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> dict[str, object]:
    """Create or reuse the one active six-task collection for a checkout."""

    paths = _paths(repo, archive_root)
    with _archive_lock(paths):
        collection = _load_collection(paths)
        if collection is None:
            collection = {
                "record_type": "review_telemetry_collection",
                "schema_version": COLLECTION_SCHEMA_VERSION,
                "purpose": "ordinary_code_review",
                "collection_id": uuid4().hex,
                "status": "active",
                "limit": COLLECTION_LIMIT,
                "created_at_utc": _now_utc(),
                "selected": {},
            }
            _write_json(paths.collection, collection)
        return _collection_status(collection, paths.root)


def status(
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> dict[str, object]:
    """Return current collection state without counting legacy records."""

    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    if not paths.collection.is_file() or paths.collection.is_symlink():
        return _inactive_status(paths.root)
    with _archive_lock(paths):
        collection = _load_collection(paths)
        if collection is None:
            return _inactive_status(paths.root)
        return _collection_status(collection, paths.root)


def begin_run(
    target: Path | str,
    *,
    repo: Path | str,
    phase: str,
    model: str,
    effort: str,
    tier: str,
    thread_file: Path | str | None = None,
    pair_id: str | None = None,
    pair_side: str | None = None,
    pair_attempt: int | None = None,
    source_digest: str | None = None,
    prompt_digest: str | None = None,
    archive_root: Path | str | None = None,
) -> str | None:
    """Reserve one run before a review call, returning its opaque run ID."""

    if phase not in PHASES:
        raise TelemetryError("invalid review phase")
    _validate_pair_metadata(pair_id, pair_side, pair_attempt, source_digest, prompt_digest)
    item = _review_item(repo, target)
    if item is None:
        return None
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    if not paths.collection.is_file() or paths.collection.is_symlink():
        return None

    cli_version = _codex_version()
    started_at = _now_utc()
    started_monotonic_ns = time.monotonic_ns()
    item_id, plan_path = item
    with _archive_lock(paths):
        collection = _load_collection(paths)
        if collection is None or collection.get("status") != "active":
            return None
        if thread_file is not None:
            thread_path = Path(thread_file)
            if thread_path.exists() or thread_path.is_symlink():
                return None
        selected = _object_mapping(collection.get("selected"), "collection.selected")
        selected_item = selected.get(item_id)
        if selected_item is None:
            if phase != "initial":
                return None
            if len(selected) >= _integer(collection.get("limit"), "collection.limit"):
                return None
            ordinal = _next_ordinal(selected)
            selected[item_id] = {
                "ordinal": ordinal,
                "plan_path": plan_path,
                "selected_at_utc": started_at,
                "observed_convergence": False,
            }
            _write_json(paths.collection, collection)
        else:
            _validate_selected_item(selected_item, item_id)

        run_id = uuid4().hex
        record = {
            "record_type": "review_telemetry_run",
            "schema_version": COLLECTION_SCHEMA_VERSION,
            "collection_id": _string(collection.get("collection_id"), "collection_id"),
            "run_id": run_id,
            "item_id": item_id,
            "phase": phase,
            "session_id": None,
            "requested_model": _bounded_text(model, "requested_model"),
            "requested_effort": _bounded_text(effort, "requested_effort"),
            "requested_tier": _bounded_text(tier, "requested_tier"),
            "cli_version": cli_version,
            "started_at_utc": started_at,
            "started_monotonic_ns": started_monotonic_ns,
            "finished_at_utc": None,
            "elapsed_seconds": None,
            "exit_status": None,
            "wrapper_exit_status": None,
            "state": "reserved",
            "final_verdict": None,
            "report": None,
            "usage": _usage_payload(EventSummary(None, _empty_usage(), None)),
        }
        if pair_id is not None:
            record.update(
                {
                    "pair_id": pair_id,
                    "pair_side": pair_side,
                    "pair_attempt": pair_attempt,
                    "source_digest": source_digest,
                    "prompt_digest": prompt_digest,
                }
            )
        _write_json(paths.runs / f"{run_id}.json", record)
        selected_item = _validate_selected_item(selected[item_id], item_id)
        selected_item["observed_convergence"] = False
        selected_item["last_run_id"] = run_id
        _write_json(paths.collection, collection)
    return run_id


def finish_run(
    run_id: str,
    *,
    repo: Path | str,
    events_file: Path | str,
    report_file: Path | str | None,
    exit_status: int,
    wrapper_exit_status: int | None = None,
    archive_root: Path | str | None = None,
) -> None:
    """Finalize one reserved run using only bounded, allowlisted call output."""

    _validate_run_id(run_id)
    if isinstance(exit_status, bool) or not isinstance(exit_status, int):
        raise TypeError("exit_status must be an integer")
    if not -255 <= exit_status <= 255:
        raise TelemetryError("exit status is outside the supported range")

    if wrapper_exit_status is None:
        wrapper_exit_status = 0 if exit_status == 0 else 1
    if wrapper_exit_status not in (0, 1):
        raise TelemetryError("invalid wrapper exit status")

    events = _read_event_summary(Path(events_file))
    report = _read_report(Path(report_file)) if report_file is not None else None
    verdict = _last_verdict(report)
    finished_at = _now_utc()
    finished_monotonic_ns = time.monotonic_ns()
    paths = _paths(repo, archive_root)
    with _archive_lock(paths):
        record_path = paths.runs / f"{run_id}.json"
        record = _load_record(record_path, "run")
        if record.get("state") != "reserved":
            return
        started_ns = record.get("started_monotonic_ns")
        elapsed = (
            (finished_monotonic_ns - started_ns) / 1_000_000_000
            if isinstance(started_ns, int) and finished_monotonic_ns >= started_ns
            else None
        )
        state = "completed" if exit_status == wrapper_exit_status == 0 else "failed"
        record.update(
            {
                "session_id": events.session_id,
                "finished_at_utc": finished_at,
                "elapsed_seconds": elapsed,
                "exit_status": exit_status,
                "wrapper_exit_status": wrapper_exit_status,
                "state": state,
                "final_verdict": verdict,
                "report": report,
                "usage": _usage_payload(events),
            }
        )
        _write_json(record_path, record)

        collection = _load_collection(paths)
        if collection is None:
            return
        selected = _object_mapping(collection.get("selected"), "collection.selected")
        item_id = _string(record.get("item_id"), "run.item_id")
        selected_item = selected.get(item_id)
        if selected_item is None:
            raise TelemetryError("run item is not selected in the active collection")
        selected_item = _validate_selected_item(selected_item, item_id)
        converged = (
            state == "completed"
            and not isinstance(record.get("pair_id"), str)
            and verdict in {"APPROVED", "PROMOTION_READY"}
        )
        selected_item["observed_convergence"] = converged
        selected_item["last_run_id"] = run_id
        _write_json(paths.collection, collection)


def record_note(
    run_id: str,
    *,
    input_file: Path | str,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> str:
    """Append a validated manager assessment without replacing prior notes."""

    _validate_run_id(run_id)
    payload = _validate_note(_read_json(Path(input_file), MAX_NOTE_BYTES))
    paths = _paths(repo, archive_root)
    with _archive_lock(paths):
        run = _load_record(paths.runs / f"{run_id}.json", "run")
        collection = _load_collection(paths)
        if collection is None:
            raise TelemetryError("collection is inactive")
        collection_id = _string(collection.get("collection_id"), "collection_id")
        if run.get("collection_id") != collection_id:
            raise TelemetryError("run does not belong to the active collection")
        note_id = uuid4().hex
        note = {
            "record_type": "review_telemetry_note",
            "schema_version": COLLECTION_SCHEMA_VERSION,
            "note_id": note_id,
            "collection_id": collection_id,
            "run_id": run_id,
            "item_id": _string(run.get("item_id"), "run.item_id"),
            "recorded_at_utc": _now_utc(),
            **payload,
        }
        _write_json(paths.notes / f"{note_id}.json", note)
    return note_id


@contextmanager
def item_pair_lock(
    item_id: str,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> Iterator[None]:
    """Serialize pair dispatch for one selected item without locking model calls globally."""

    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise TelemetryError("invalid telemetry item ID")
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    _ensure_private_directory(paths.pair_locks)
    lock_path = paths.pair_locks / f"{item_id}.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "r+") as lock_file:
            descriptor = -1
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield
    except OSError as exc:
        raise TelemetryError("telemetry item lock failed") from exc
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


def read_pair(
    item_id: str,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> dict[str, object] | None:
    """Read the current pair state for an item, ignoring older namespaces."""

    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise TelemetryError("invalid telemetry item ID")
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    if paths.pairs.is_symlink() or (paths.pairs.exists() and not paths.pairs.is_dir()):
        raise TelemetryError("telemetry pair directory is unsafe")
    pair_path = paths.pairs / f"{item_id}.json"
    if not pair_path.exists():
        return None
    return _load_record(pair_path, "pair")


def runs_for_pair(
    pair_id: str,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> list[dict[str, object]]:
    """Return current-collection attempts linked to a pair, including reservations."""

    _validate_run_id(pair_id)
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    if not paths.runs.is_dir() or paths.runs.is_symlink():
        return []
    result: list[dict[str, object]] = []
    for path in sorted(paths.runs.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise TelemetryError("telemetry run directory contains an unsafe entry")
        record = _load_record(path, "run")
        if record.get("pair_id") == pair_id:
            result.append(record)
    return result


def write_pair(
    pair: Mapping[str, object],
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> None:
    """Atomically write one current pair record while its item lock is held."""

    item_id = _string(pair.get("item_id"), "pair.item_id")
    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise TelemetryError("invalid telemetry item ID")
    if pair.get("record_type") != "review_telemetry_pair":
        raise TelemetryError("invalid pair record")
    if pair.get("schema_version") != COLLECTION_SCHEMA_VERSION:
        raise TelemetryError("invalid pair schema")
    paths = _paths(repo, archive_root)
    _write_json(paths.pairs / f"{item_id}.json", pair)


def read_run(
    run_id: str,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> dict[str, object]:
    """Read one allowlisted run record for coordinator report composition."""

    _validate_run_id(run_id)
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    if paths.runs.is_symlink() or (paths.runs.exists() and not paths.runs.is_dir()):
        raise TelemetryError("telemetry run directory is unsafe")
    return _load_record(paths.runs / f"{run_id}.json", "run")


def set_observed_convergence(
    item_id: str,
    converged: bool,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> None:
    """Set the selected item's latest observed state after a complete paired first pass."""

    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise TelemetryError("invalid telemetry item ID")
    paths = _paths(repo, archive_root)
    with _archive_lock(paths):
        collection = _load_collection(paths)
        if collection is None:
            raise TelemetryError("collection is inactive")
        selected = _object_mapping(collection.get("selected"), "collection.selected")
        item = selected.get(item_id)
        if item is None:
            raise TelemetryError("pair item is not selected")
        selected_item = _validate_selected_item(item, item_id)
        selected_item["observed_convergence"] = converged
        _write_json(paths.collection, collection)


def admit_item(
    target: Path | str,
    *,
    repo: Path | str,
    archive_root: Path | str | None = None,
) -> tuple[str, int, bool] | None:
    """Atomically admit one new planned item, returning collection, ordinal and created state."""

    item = _review_item(repo, target)
    if item is None:
        return None
    item_id, plan_path = item
    paths = _paths(repo, archive_root)
    _check_archive_root(paths)
    with _archive_lock(paths):
        collection = _load_collection(paths)
        if collection is None:
            return None
        collection_id = _string(collection.get("collection_id"), "collection_id")
        selected = _object_mapping(collection.get("selected"), "collection.selected")
        existing = selected.get(item_id)
        if existing is not None:
            ordinal = _integer(
                _validate_selected_item(existing, item_id).get("ordinal"),
                f"selected.{item_id}.ordinal",
            )
            return collection_id, ordinal, False
        limit = _integer(collection.get("limit"), "collection.limit")
        if len(selected) >= limit:
            return None
        ordinal = _next_ordinal(selected)
        selected[item_id] = {
            "ordinal": ordinal,
            "plan_path": plan_path,
            "selected_at_utc": _now_utc(),
            "observed_convergence": False,
        }
        _write_json(paths.collection, collection)
        return collection_id, ordinal, True


def _paths(repo: Path | str, override: Path | str | None) -> TelemetryPaths:
    selected_override = override or os.environ.get("NADILI_REVIEW_TELEMETRY_ROOT")
    if selected_override is not None:
        root = Path(selected_override)
        if not root.is_absolute():
            raise TelemetryError("archive root must be absolute")
    else:
        git_common = _git_common_directory(Path(repo))
        key = hashlib.sha256(str(git_common).encode("utf-8")).hexdigest()[:24]
        root = _DEFAULT_ROOT / key
    return TelemetryPaths(
        root=root,
        collection=root / _COLLECTION_FILENAME,
        runs=root / _RUNS_DIRECTORY,
        notes=root / _NOTES_DIRECTORY,
        pairs=root / _PAIRS_DIRECTORY,
        pair_locks=root / _PAIR_LOCKS_DIRECTORY,
        lock=root / ".lock",
    )


def _check_archive_root(paths: TelemetryPaths) -> None:
    if paths.root.is_symlink() or (paths.root.exists() and not paths.root.is_dir()):
        raise TelemetryError("telemetry archive is unavailable")


def _git_common_directory(repo: Path | str) -> Path:
    requested = Path(repo).resolve()
    try:
        root_result = subprocess.run(  # noqa: S603 -- fixed git argv and validated local path
            ("git", "-C", str(requested), "rev-parse", "--show-toplevel"),  # noqa: S607 -- fixed git executable
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        root = Path(root_result.stdout.strip()).resolve()
        common_result = subprocess.run(  # noqa: S603 -- fixed git argv and validated local path
            ("git", "-C", str(root), "rev-parse", "--git-common-dir"),  # noqa: S607 -- fixed git executable
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TelemetryError("git common directory unavailable") from exc
    raw_common = common_result.stdout.strip()
    if not raw_common or "\n" in raw_common:
        raise TelemetryError("git common directory unavailable")
    common = Path(raw_common)
    return (root / common).resolve() if not common.is_absolute() else common.resolve()


def _review_item(repo: Path | str, target: Path | str) -> tuple[str, str] | None:
    requested_root = Path(repo).resolve()
    try:
        root_result = subprocess.run(  # noqa: S603 -- fixed git argv and validated local path
            ("git", "-C", str(requested_root), "rev-parse", "--show-toplevel"),  # noqa: S607 -- fixed git executable
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TelemetryError("review worktree unavailable") from exc
    root = Path(root_result.stdout.strip()).resolve()
    candidate = Path(target)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = candidate.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return None
    parts = relative.parts
    if len(parts) != 4 or parts[0:2] != ("docs", "work") or parts[3] != "plan.md":
        return None
    item_id = parts[2]
    if not ITEM_ID_PATTERN.fullmatch(item_id):
        return None
    return item_id, relative.as_posix()


def eligible_item(repo: Path | str, target: Path | str) -> tuple[str, str] | None:
    """Return the supported planned item identity without enrolling it."""

    return _review_item(repo, target)


def _collection_status(collection: Mapping[str, object], root: Path) -> dict[str, object]:
    selected = _object_mapping(collection.get("selected"), "collection.selected")
    runs = _current_runs(collection, root)
    tasks: list[dict[str, object]] = []
    counts = {"selected": 0, "converged": 0, "incomplete": 0}
    for item_id, raw_item in sorted(selected.items()):
        item = _validate_selected_item(raw_item, item_id)
        item_runs = [run for run in runs if run.get("item_id") == item_id]
        converged = item.get("observed_convergence") is True
        if converged:
            state = "converged"
        else:
            state = (
                "incomplete"
                if any(run.get("state") in {"reserved", "failed"} for run in item_runs)
                else "selected"
            )
        counts[state] += 1
        tasks.append(
            {
                "item_id": item_id,
                "ordinal": _integer(item.get("ordinal"), f"selected.{item_id}.ordinal"),
                "state": state,
                "last_run_id": item.get("last_run_id"),
            }
        )
    return {
        "status": "active",
        "collection_id": _string(collection.get("collection_id"), "collection_id"),
        "archive_root": str(root),
        "limit": _integer(collection.get("limit"), "collection.limit"),
        "admitted": len(selected),
        "remaining": max(0, _integer(collection.get("limit"), "collection.limit") - len(selected)),
        "run_count": len(runs),
        "tasks": tasks,
        "counts": counts,
    }


def _inactive_status(root: Path) -> dict[str, object]:
    return {
        "status": "inactive",
        "archive_root": str(root),
        "limit": COLLECTION_LIMIT,
        "admitted": 0,
        "remaining": COLLECTION_LIMIT,
        "run_count": 0,
        "tasks": [],
        "counts": {"selected": 0, "converged": 0, "incomplete": 0},
    }


def _current_runs(collection: Mapping[str, object], root: Path) -> list[dict[str, object]]:
    collection_id = _string(collection.get("collection_id"), "collection_id")
    runs_dir = root / _RUNS_DIRECTORY
    if not runs_dir.is_dir() or runs_dir.is_symlink():
        return []
    runs: list[dict[str, object]] = []
    for path in sorted(runs_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise TelemetryError("telemetry run directory contains an unsafe entry")
        record = _load_record(path, "run")
        if record.get("collection_id") == collection_id:
            runs.append(record)
    return runs


def _load_collection(paths: TelemetryPaths) -> dict[str, object] | None:
    if not paths.collection.exists():
        return None
    payload = _read_json(paths.collection)
    collection = _object_mapping(payload, "collection")
    if collection.get("record_type") != "review_telemetry_collection":
        raise TelemetryError("unsupported collection record")
    if collection.get("schema_version") != COLLECTION_SCHEMA_VERSION:
        raise TelemetryError("unsupported collection schema")
    if collection.get("purpose") != "ordinary_code_review":
        raise TelemetryError("unsupported collection purpose")
    if collection.get("status") != "active":
        raise TelemetryError("collection state is not active")
    _validate_run_id(_string(collection.get("collection_id"), "collection_id"))
    if collection.get("limit") != COLLECTION_LIMIT:
        raise TelemetryError("collection limit is invalid")
    _object_mapping(collection.get("selected"), "collection.selected")
    return collection


def _load_record(path: Path, label: str) -> dict[str, object]:
    payload = _read_json(path)
    record = _object_mapping(payload, label)
    if record.get("schema_version") != COLLECTION_SCHEMA_VERSION:
        raise TelemetryError(f"unsupported {label} schema")
    expected_types = {
        "run": "review_telemetry_run",
        "note": "review_telemetry_note",
        "pair": "review_telemetry_pair",
    }
    expected_type = expected_types.get(label)
    if expected_type is None:
        raise TelemetryError("unsupported telemetry record type")
    if record.get("record_type") != expected_type:
        raise TelemetryError(f"unsupported {label} record")
    return record


def _read_json(path: Path, maximum: int = MAX_JSON_BYTES) -> object:
    if path.is_symlink() or not path.is_file():
        raise TelemetryError("telemetry JSON file is unavailable")
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError as exc:
        raise TelemetryError("telemetry JSON file is unreadable") from exc
    if len(raw) > maximum:
        raise TelemetryError("telemetry JSON file exceeds its bound")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelemetryError("telemetry JSON is malformed") from exc


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _ensure_private_directory(path.parent)
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TelemetryError("telemetry record is not JSON-serializable") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise TelemetryError("telemetry record exceeds its bound")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise TelemetryError("telemetry record write failed") from exc
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = path.lstat()
        if not mode or path.is_symlink() or not path.is_dir():
            raise TelemetryError("telemetry directory is unsafe")
        os.chmod(path, 0o700)
    except OSError as exc:
        raise TelemetryError("telemetry directory is unavailable") from exc


@contextmanager
def _archive_lock(paths: TelemetryPaths) -> Iterator[None]:
    _ensure_private_directory(paths.root)
    descriptor = os.open(
        paths.lock,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "r+") as lock_file:
            descriptor = -1
            os.chmod(paths.lock, 0o600)
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield
    except OSError as exc:
        raise TelemetryError("telemetry lock failed") from exc
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


def _read_event_summary(path: Path) -> EventSummary:
    session_id: str | None = None
    usage = _empty_usage()
    usage_source: str | None = None
    completion_count = 0
    try:
        with path.open("rb") as stream:
            while line := stream.readline(MAX_EVENT_BYTES + 1):
                if len(line) > MAX_EVENT_BYTES:
                    # Drain oversized tool-output lines without losing the final usage event.
                    while not line.endswith(b"\n"):
                        line = stream.readline(MAX_EVENT_BYTES + 1)
                        if not line:
                            break
                    continue
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "thread.started" and session_id is None:
                    candidate = event.get("thread_id")
                    if isinstance(candidate, str) and 0 < len(candidate) <= 256:
                        session_id = candidate
                if event.get("type") != "turn.completed":
                    continue
                completion_count += 1
                candidates = list(_usage_candidates(event))
                if completion_count == 1 and len(candidates) == 1:
                    parsed = _parse_usage(candidates[0])
                    if parsed is not None:
                        usage = parsed
                        usage_source = "codex_event_stream"
    except OSError:
        return EventSummary(session_id, _empty_usage(), None)
    if completion_count != 1:
        usage, usage_source = _empty_usage(), None
    return EventSummary(session_id, usage, usage_source)


def _usage_candidates(event: Mapping[str, object]) -> Iterator[Mapping[str, object]]:
    direct = event.get("usage")
    if isinstance(direct, dict):
        yield direct
    for parent_name in ("response", "turn", "item"):
        parent = event.get(parent_name)
        if isinstance(parent, dict) and isinstance(parent.get("usage"), dict):
            yield parent["usage"]


def _parse_usage(value: Mapping[str, object]) -> dict[str, int | None] | None:
    usage = _empty_usage()
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens"),
    }
    found = False
    for name, keys in aliases.items():
        for key in keys:
            candidate = value.get(key)
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and 0 <= candidate <= 1_000_000_000
            ):
                usage[name] = candidate
                found = True
                break
    input_details = value.get("input_tokens_details")
    if isinstance(input_details, dict):
        cached = input_details.get("cached_tokens")
        if (
            usage["cached_input_tokens"] is None
            and isinstance(cached, int)
            and not isinstance(cached, bool)
        ):
            usage["cached_input_tokens"] = cached if 0 <= cached <= 1_000_000_000 else None
            found = usage["cached_input_tokens"] is not None or found
    output_details = value.get("output_tokens_details")
    if isinstance(output_details, dict):
        reasoning = output_details.get("reasoning_tokens")
        if (
            usage["reasoning_tokens"] is None
            and isinstance(reasoning, int)
            and not isinstance(reasoning, bool)
        ):
            usage["reasoning_tokens"] = reasoning if 0 <= reasoning <= 1_000_000_000 else None
            found = usage["reasoning_tokens"] is not None or found
    if not found:
        return None
    input_tokens = usage["input_tokens"]
    cached_tokens = usage["cached_input_tokens"]
    if input_tokens is not None and cached_tokens is not None and cached_tokens > input_tokens:
        usage["cached_input_tokens"] = None
    output_tokens = usage["output_tokens"]
    reasoning_tokens = usage["reasoning_tokens"]
    if (
        output_tokens is not None
        and reasoning_tokens is not None
        and reasoning_tokens > output_tokens
    ):
        usage["reasoning_tokens"] = None
    return usage


def _read_report(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_REPORT_BYTES:
        return None
    return _redact_text(raw.decode("utf-8", errors="replace"))


def _last_verdict(report: str | None) -> str | None:
    if report is None:
        return None
    matches = _VERDICT_PATTERN.findall(report)
    nonempty_lines = [line.strip() for line in report.splitlines() if line.strip()]
    if not matches or not nonempty_lines or nonempty_lines[-1] != matches[-1]:
        return None
    verdict = matches[-1]
    return verdict if isinstance(verdict, str) else None


def _usage_payload(summary: EventSummary) -> dict[str, object]:
    missing = [name for name, value in summary.usage.items() if value is None]
    return {
        **summary.usage,
        "source": summary.usage_source,
        "missing": missing,
        "semantics": "turn-local; cached input is within input; reasoning is within output",
    }


def _empty_usage() -> dict[str, int | None]:
    return {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }


def _validate_note(value: object) -> dict[str, object]:
    note = _object_mapping(value, "note")
    if set(note) != {"assessor", "findings"}:
        raise TelemetryError("note must contain assessor and findings only")
    assessor = _bounded_text(note.get("assessor"), "assessor", 128)
    raw_findings = note.get("findings")
    if not isinstance(raw_findings, list) or len(raw_findings) > MAX_FINDINGS:
        raise TelemetryError("note findings must be a bounded list")
    findings: list[dict[str, object]] = []
    for raw_finding in raw_findings:
        finding = _object_mapping(raw_finding, "finding")
        finding_id = finding.get("finding_id", finding.get("id"))
        normalized_finding_id = _bounded_text(finding_id, "finding_id", 128)
        if not _FINDING_ID_PATTERN.fullmatch(normalized_finding_id):
            raise TelemetryError("invalid finding_id")
        source_reference = finding.get("source_reference", finding.get("source"))
        source_excerpt = finding.get("source_excerpt", finding.get("excerpt"))
        checked: dict[str, object] = {
            "finding_id": normalized_finding_id,
            "source_reference": _redacted_text(source_reference, "source_reference", 512),
            "source_excerpt": _redacted_text(source_excerpt, "source_excerpt", MAX_TEXT_FIELD),
            "claimed_severity": _choice(
                finding.get("claimed_severity"), "claimed_severity", ("P0", "P1", "P2", "P3")
            ),
            "checked_severity": _choice(
                finding.get("checked_severity"), "checked_severity", ("P0", "P1", "P2", "P3")
            ),
            "judgment": _choice(
                finding.get("judgment"), "judgment", ("confirmed", "rejected", "uncertain")
            ),
            "disposition": _choice(
                finding.get("disposition"), "disposition", ("fixed", "deferred", "no-action")
            ),
            "evidence": _redacted_text(finding.get("evidence"), "evidence", 1024),
        }
        findings.append(checked)
    return {"assessor": assessor, "findings": findings}


def _redacted_text(value: object, label: str, maximum: int) -> str:
    text = _bounded_text(value, label, maximum)
    return _redact_text(text)


def _choice(value: object, label: str, choices: Sequence[str]) -> str:
    text = _bounded_text(value, label, 32)
    if text not in choices:
        raise TelemetryError(f"invalid {label}")
    return text


def _validate_selected_item(value: object, item_id: str) -> dict[str, object]:
    item = _object_mapping(value, f"selected.{item_id}")
    if not isinstance(item.get("ordinal"), int) or isinstance(item.get("ordinal"), bool):
        raise TelemetryError("selected item ordinal is invalid")
    return item


def _next_ordinal(selected: Mapping[str, object]) -> int:
    ordinals = [
        _integer(
            _object_mapping(value, f"selected.{key}").get("ordinal"), f"selected.{key}.ordinal"
        )
        for key, value in selected.items()
    ]
    return max(ordinals, default=0) + 1


def _validate_run_id(value: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise TelemetryError("invalid telemetry ID")


def _validate_pair_metadata(
    pair_id: str | None,
    pair_side: str | None,
    pair_attempt: int | None,
    source_digest: str | None,
    prompt_digest: str | None,
) -> None:
    metadata = (pair_side, pair_attempt, source_digest, prompt_digest)
    if pair_id is None:
        if any(value is not None for value in metadata):
            raise TelemetryError("incomplete pair metadata")
        return
    _validate_run_id(pair_id)
    if pair_side not in _PAIR_SIDES:
        raise TelemetryError("invalid pair side")
    if not isinstance(pair_attempt, int) or isinstance(pair_attempt, bool) or pair_attempt < 1:
        raise TelemetryError("invalid pair attempt")
    for digest in (source_digest, prompt_digest):
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise TelemetryError("invalid pair digest")


def _bounded_text(value: object, label: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        raise TelemetryError(f"invalid {label}")
    return value


def _string(value: object, label: str) -> str:
    return _bounded_text(value, label, 512)


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TelemetryError(f"invalid {label}")
    return value


def _object_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TelemetryError(f"invalid {label}")
    return value


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _redact_text(text: str) -> str:
    from nadili_runtime.log_redaction import redact_text

    return redact_text(text)


def _codex_version() -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed codex metadata argv, no shell
            ("codex", "--version"),  # noqa: S607 -- fixed codex executable
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    version = completed.stdout.strip().splitlines()
    candidate = version[0].strip() if version else ""
    return candidate if _SAFE_VERSION_PATTERN.fullmatch(candidate) else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    activate_parser = subparsers.add_parser("activate")
    _storage_arguments(activate_parser)
    activate_parser.add_argument("--repo", type=Path, default=Path.cwd())

    status_parser = subparsers.add_parser("status")
    _storage_arguments(status_parser)
    status_parser.add_argument("--repo", type=Path, default=Path.cwd())

    begin_parser = subparsers.add_parser("begin")
    _storage_arguments(begin_parser)
    begin_parser.add_argument("--repo", required=True, type=Path)
    begin_parser.add_argument("--target", required=True, type=Path)
    begin_parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    begin_parser.add_argument("--model", required=True)
    begin_parser.add_argument("--effort", required=True)
    begin_parser.add_argument("--tier", required=True)
    begin_parser.add_argument("--thread-file", type=Path)

    finish_parser = subparsers.add_parser("finish")
    _storage_arguments(finish_parser)
    finish_parser.add_argument("--repo", required=True, type=Path)
    finish_parser.add_argument("--run-id", required=True)
    finish_parser.add_argument("--events-file", required=True, type=Path)
    finish_parser.add_argument("--report-file", type=Path)
    finish_parser.add_argument("--exit-status", required=True, type=int)
    finish_parser.add_argument("--wrapper-exit-status", type=int, choices=(0, 1))

    note_parser = subparsers.add_parser("note")
    _storage_arguments(note_parser)
    note_parser.add_argument("--repo", type=Path, default=Path.cwd())
    note_parser.add_argument("--run-id", required=True)
    note_parser.add_argument("--input-file", required=True, type=Path)
    return parser


def _storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--archive-root", type=Path)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "activate":
            print(
                json.dumps(activate(repo=args.repo, archive_root=args.archive_root), sort_keys=True)
            )
        elif args.command == "status":
            print(
                json.dumps(status(repo=args.repo, archive_root=args.archive_root), sort_keys=True)
            )
        elif args.command == "begin":
            run_id = begin_run(
                args.target,
                repo=args.repo,
                phase=args.phase,
                model=args.model,
                effort=args.effort,
                tier=args.tier,
                thread_file=args.thread_file,
                archive_root=args.archive_root,
            )
            if run_id is not None:
                print(run_id)
        elif args.command == "finish":
            finish_run(
                args.run_id,
                repo=args.repo,
                events_file=args.events_file,
                report_file=args.report_file,
                exit_status=args.exit_status,
                wrapper_exit_status=args.wrapper_exit_status,
                archive_root=args.archive_root,
            )
        elif args.command == "note":
            note_id = record_note(
                args.run_id,
                input_file=args.input_file,
                repo=args.repo,
                archive_root=args.archive_root,
            )
            print(json.dumps({"status": "recorded", "note_id": note_id}, sort_keys=True))
        return 0
    except (OSError, TelemetryError) as exc:
        print(f"review telemetry warning: {type(exc).__name__}", file=sys.stderr)
        return 0 if args.command in {"begin", "finish"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
