#!/usr/bin/env python3
"""Refuse shared-state commits whose effective work-item contract omits coverage."""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.hotfix_contract import validate_hotfix_text

RESOURCE_IDS: frozenset[str] = frozenset(
    {
        "origin-main",
        "published-tags",
        "version-reservations",
        "heavy-lease",
        "local-env",
        "local-compose",
        "primary-checkout",
        "production-host",
        "sibling-delivery-path",
        "unclassified",
    }
)
TRIGGER_PREFIXES = (
    "scripts/",
    "infra/",
    ".github/workflows/",
    ".claude/skills/nadili-process/",
)
PLACEHOLDERS = frozenset({"TBD", "TODO", "N/A", "-"})
BRANCH_ITEM_RE = re.compile(r"^work/(?P<item>.+)$")
INTERLEAVING_HEADING = "### Interleaving"
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")
# Resource | Step | What can change under us | Response -- contract interleaving requirement.
REQUIRED_COLUMNS = 4


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is not on PATH")
    result = subprocess.run(  # noqa: S603 — fixed argv, absolute binary, no shell
        [git, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _staged_paths() -> list[str]:
    out = _git("diff", "--cached", "--no-renames", "--name-only", "--diff-filter=ACMRD")
    return [line for line in out.splitlines() if line]


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def _branch_item(branch: str) -> str | None:
    match = BRANCH_ITEM_RE.match(branch)
    return match.group("item") if match else None


def _script_glob_matches(path: str, pattern: str) -> bool:
    if not path.startswith("scripts/"):
        return False
    return "/" not in path.removeprefix("scripts/") and fnmatch.fnmatchcase(path, pattern)


def _resources_for_path(path: str) -> set[str]:
    resources: set[str] = set()
    if _script_glob_matches(path, "scripts/release*") or path == "scripts/next_version.py":
        resources.update({"published-tags", "version-reservations"})
    if _script_glob_matches(path, "scripts/gate*") or path == "scripts/lib/gate-slots.sh":
        resources.add("heavy-lease")
    if path == "scripts/worktree-new.sh":
        resources.update({"primary-checkout", "origin-main"})
    if path == "scripts/backend.sh" or path.startswith("infra/services/"):
        resources.update({"local-compose", "local-env"})
    if _script_glob_matches(path, "scripts/prod-*") or path.startswith("infra/bootstrap/"):
        resources.add("production-host")
    if (
        _script_glob_matches(path, "scripts/pre-commit*.sh")
        or _script_glob_matches(path, "scripts/*-check.sh")
        or _script_glob_matches(path, "scripts/check_*.py")
        or path.startswith(".claude/skills/nadili-process/")
    ):
        resources.add("sibling-delivery-path")

    if resources:
        return resources
    if any(path.startswith(prefix) for prefix in TRIGGER_PREFIXES):
        return {"unclassified"}
    return set()


def _visible_lines(plan_text: str) -> list[str]:
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in plan_text.splitlines():
        if fence is not None:
            marker, width = fence
            if re.fullmatch(rf" {{0,3}}{re.escape(marker)}{{{width},}}[ \t]*", line):
                fence = None
            continue
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if opening is not None:
            marker, info = opening.groups()
            if marker[0] != "`" or "`" not in info:
                fence = (marker[0], len(marker))
                continue
        lines.append(line)
    return lines


def _cell_content(cell: str) -> str:
    """Return a cell's content with Markdown code-span formatting removed.

    Authors write `sibling-delivery-path` as a code span, exactly as the plan's own path map
    does. Comparing the raw cell against the closed ID set would reject that and force bare
    identifiers into a table where every other technical token is formatted -- a rule about
    typography, not about correctness. The same normalisation decides emptiness and placeholders,
    so `TBD` cannot buy completeness that TBD is denied, and a cell holding nothing but code-span
    markers reads as the empty answer it is.
    """

    return cell.strip().strip("`").strip()


def _table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return None
    # Split on unescaped pipes only. A `\|` is a literal pipe inside one cell, and splitting on it
    # would invent a column: every later cell shifts left, and a row whose last answer is empty
    # presents a full set of filled cells to the completeness check.
    cells = [cell.replace("\\|", "|") for cell in UNESCAPED_PIPE_RE.split(stripped)]
    if cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _is_separator_row(cells: list[str]) -> bool:
    return len(cells) >= 4 and all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells)


def _interleaving_table(plan_text: str) -> tuple[bool, list[list[str]] | None]:
    lines = _visible_lines(plan_text)
    found_heading = False
    for heading_index, line in enumerate(lines):
        if line.strip() != INTERLEAVING_HEADING:
            continue
        found_heading = True
        # Scan to the first Resource table inside this subsection rather than demanding one on
        # the next non-blank line. An author should be able to say WHICH classes the commit
        # triggered and why before tabulating them -- that reasoning is the point of the section,
        # and forbidding it would push every plan towards a bare table. The scan stops at the next
        # heading of any level so a table belonging to a later section can never be borrowed.
        table_index = heading_index + 1
        while table_index < len(lines):
            stripped = lines[table_index].strip()
            if stripped.startswith("#"):
                table_index = len(lines)
                break
            if _table_row(stripped) is not None:
                break
            table_index += 1
        if table_index >= len(lines):
            continue

        header = _table_row(lines[table_index])
        if header is None or not header or header[0] != "Resource":
            continue
        separator = _table_row(lines[table_index + 1]) if table_index + 1 < len(lines) else None
        if separator is None or not _is_separator_row(separator):
            continue

        rows: list[list[str]] = []
        table_index += 2
        while table_index < len(lines):
            if not lines[table_index].strip():
                break
            row = _table_row(lines[table_index])
            if row is None:
                break
            rows.append(row)
            table_index += 1
        return True, rows
    return found_heading, None


def _is_placeholder(cell: str) -> bool:
    return _cell_content(cell).upper() in PLACEHOLDERS


def violations(
    staged: list[str],
    branch: str,
    plan_text: str | None,
    contract_path: str | None = None,
) -> list[str]:
    """Return one message per broken interleaving rule for a staged change set."""
    branch_item = _branch_item(branch)
    if branch_item is None:
        return []

    triggered: set[str] = set()
    for path in staged:
        triggered.update(_resources_for_path(path))
    if not triggered:
        return []

    plan_path = contract_path or f"docs/work/{branch_item}/plan.md"
    if plan_text is None:
        return [
            f"staged shared-state paths trigger interleaving checks, but {plan_path} is absent "
            "from the effective commit blob."
        ]

    found_heading, rows = _interleaving_table(plan_text)
    if not found_heading:
        return [f"{plan_path} must contain a {INTERLEAVING_HEADING} subsection."]
    if rows is None:
        return [
            f"{INTERLEAVING_HEADING} must be followed by a table whose first column is Resource."
        ]
    if not rows:
        return [f"{INTERLEAVING_HEADING} table must contain at least one data row."]

    covered: set[str] = set()
    problems: list[str] = []
    for row in rows:
        resource = _cell_content(row[0]) if row else ""
        if resource and resource not in RESOURCE_IDS:
            problems.append(f"{INTERLEAVING_HEADING} contains unknown Resource ID '{resource}'.")
        if len(row) != REQUIRED_COLUMNS or any(
            not _cell_content(cell) or _is_placeholder(cell) for cell in row
        ):
            continue
        if resource in RESOURCE_IDS:
            covered.add(resource)

    missing = sorted(triggered - covered)
    if missing:
        problems.append(
            f"{INTERLEAVING_HEADING} is missing a complete row for triggered resource(s): "
            f"{', '.join(missing)}."
        )
    return problems


def _effective_text(staged: list[str], path: str) -> str | None:
    ref = ":" if path in staged else "HEAD:"
    try:
        return _git("show", f"{ref}{path}")
    except subprocess.CalledProcessError:
        return None


def main() -> int:
    staged = _staged_paths()
    if not staged:
        return 0

    branch = _current_branch()
    item = _branch_item(branch)
    if item is None:
        return 0

    plan_path = f"docs/work/{item}/plan.md"
    hotfix_path = f"docs/work/{item}/hotfix.md"
    plan_text = _effective_text(staged, plan_path)
    hotfix_text = _effective_text(staged, hotfix_path)
    if plan_text is not None and hotfix_text is not None:
        print("Plan interleaving check failed:", file=sys.stderr)
        print("  - hotfix.md cannot coexist with plan.md for the same item.", file=sys.stderr)
        return 1
    if plan_text is not None:
        effective_contract = plan_text
        effective_contract_path = plan_path
    elif hotfix_text is not None:
        hotfix_problems = validate_hotfix_text(hotfix_text, item)
        if hotfix_problems:
            print("Plan interleaving check failed:", file=sys.stderr)
            for problem in hotfix_problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        effective_contract = hotfix_text
        effective_contract_path = hotfix_path
    else:
        effective_contract = None
        effective_contract_path = plan_path

    problems = violations(staged, branch, effective_contract, effective_contract_path)
    if not problems:
        return 0

    print("Plan interleaving check failed:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
