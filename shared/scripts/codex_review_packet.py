"""Build review deltas from Git snapshots, independently of model instructions."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

MAX_PATCH_BYTES = 128 * 1024


class PacketError(Exception):
    """The candidate or its review lineage cannot be verified."""


@dataclass(frozen=True)
class ReviewPacket:
    tree: str
    base: str
    mode: str
    reason: str
    correction: str | None
    base_changes: str


def candidate_tree(git: Callable[..., str]) -> str:
    """Require explicit staging; never stage untracked files on the caller's behalf."""
    if git("diff", "--name-only") or git("ls-files", "--others", "--exclude-standard"):
        raise PacketError(
            "stage candidate paths and remove unrelated untracked files before review"
        )
    if git("ls-files", "-u"):
        raise PacketError("resolve candidate conflicts before review")
    return git("write-tree")


def build_packet(
    git: Callable[..., str],
    *,
    base: str,
    previous: dict[str, object] | None,
    exhaustive_reason: str | None,
) -> ReviewPacket:
    """Separate item corrections from upstream changes, including after a rebase."""
    tree = candidate_tree(git)
    if previous is None:
        return ReviewPacket(tree, base, "initial", "first_review", None, "")
    context = previous.get("review_context")
    if not isinstance(context, dict):
        raise PacketError(
            "previous review lacks review_context; supply --previous-tree/--previous-base"
        )
    old_tree = context.get("tree")
    old_base = context.get("diff_base")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40,64}", value)
        for value in (old_tree, old_base)
    ):
        raise PacketError("previous review has invalid Git snapshot metadata")
    old_tree = git("rev-parse", "--verify", f"{old_tree}^{{tree}}")
    old_base = git("rev-parse", "--verify", f"{old_base}^{{commit}}")
    paths = sorted(
        set(git("diff", "--no-renames", "--name-only", "-z", old_base, old_tree).split("\0"))
        | set(git("diff", "--no-renames", "--name-only", "-z", base, tree).split("\0")) - {""}
    )
    paths = [f":(literal){path}" for path in paths if path]
    correction = (
        git("diff", "--no-ext-diff", "--no-textconv", "--binary", old_tree, tree, "--", *paths)
        if paths
        else ""
    )
    base_changes = ""
    if old_base != base:
        # Names identify dependency changes without transporting unrelated upstream file bodies.
        names = git("diff", "--no-renames", "--name-only", old_base, base)
        overlaps = (
            git("diff", "--no-ext-diff", "--no-textconv", old_base, base, "--", *paths)
            if paths
            else ""
        )
        base_changes = (
            f"Upstream range: {old_base}..{base}\nChanged paths:\n{names}\nOverlap:\n{overlaps}"
        )
    if exhaustive_reason:
        return ReviewPacket(tree, base, "exhaustive", exhaustive_reason, None, base_changes)
    if len((correction + base_changes).encode("utf-8")) > MAX_PATCH_BYTES:
        raise PacketError(
            "review packet exceeds 128 KiB; use --exhaustive-reason with prior findings retained"
        )
    is_rebase_refresh = (
        previous.get("verdict") == "APPROVED" and old_base != base and not correction.strip()
    )
    mode = "rebase_refresh" if is_rebase_refresh else "correction"
    reason = "rebase_only" if is_rebase_refresh else "prior_review"
    return ReviewPacket(tree, base, mode, reason, correction, base_changes)


def read_record(path: Path, *, maximum_bytes: int = 128 * 1024) -> dict[str, object]:
    """Read bounded review evidence; model output is never treated as an instruction."""
    try:
        if path.stat().st_size > maximum_bytes:
            raise PacketError("previous review exceeds bounded evidence size")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PacketError("cannot read previous review evidence") from exc
    if not isinstance(payload, dict):
        raise PacketError("previous review must be an object")
    return payload


@contextmanager
def review_store(state: Path) -> Iterator[Path]:
    """Serialize a single item's reviews; another item has an independent lock."""
    directory = state.with_suffix(".reviews")
    if directory.is_symlink():
        raise PacketError("review evidence directory must not be a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PacketError("another review for this item is running") from exc
        yield directory
    finally:
        os.close(descriptor)


def save_record(directory: Path, attempt_id: str, record: dict[str, object]) -> None:
    """Keep every completed verdict and atomically advance the correction input."""
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    if len(payload.encode("utf-8")) > 128 * 1024:
        raise PacketError("review evidence exceeds bounded record size")
    name = hashlib.sha256(attempt_id.encode()).hexdigest() + ".json"
    for destination in (directory / name, directory / "latest.json"):
        descriptor, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload + "\n")
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)


def review_metrics(state_directory: Path, limit: int) -> list[dict[str, object]]:
    """Summarize completed verdicts; never conflate them with transport attempts or QA."""
    latest_files = sorted(
        state_directory.glob("*.reviews/latest.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:limit]
    rows: list[dict[str, object]] = []
    for latest in latest_files:
        counts = {"initial_miss": 0, "fix_regression": 0, "unresolved": 0, "base_change": 0}
        verdicts = 0
        elapsed_ms = 0
        for path in latest.parent.glob("*.json"):
            if path.name == "latest.json":
                continue
            record = read_record(path)
            context = record.get("review_context")
            findings = record.get("findings")
            if not isinstance(context, dict) or not isinstance(findings, list):
                raise PacketError("review history has invalid structured evidence")
            duration = context.get("elapsed_ms")
            if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
                raise PacketError("review history has invalid elapsed time")
            elapsed_ms += duration
            verdicts += 1
            for finding in findings:
                if not isinstance(finding, dict):
                    raise PacketError("review history has invalid finding")
                origin = finding.get("origin")
                if not isinstance(origin, str) or origin not in counts:
                    raise PacketError("review history has invalid finding origin")
                if finding.get("priority") in {"P0", "P1", "P2"} and (
                    origin != "initial_miss" or context.get("mode") != "initial"
                ):
                    counts[origin] += 1
        rows.append(
            {
                "item": latest.parent.stem,
                "completed_verdicts": verdicts,
                "review_elapsed_ms": elapsed_ms,
                "later_blocker_origins": counts,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report the most recently reviewed items from private review history."
    )
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or not args.state_directory.is_dir():
        parser.error("provide an existing state directory and a limit from 1 to 100")
    try:
        print(json.dumps(review_metrics(args.state_directory, args.limit), indent=2))
    except PacketError as exc:
        parser.exit(2, f"codex-review-report: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
