#!/usr/bin/env python3
"""Answer one question before an agent claims an item is being implemented.

``In Progress`` in Linear encodes a precondition: the item's worktree exists and either its
approved plan or canonical hotfix contract is inside it. An agent that sets the status first and discovers the problem afterwards has already
lied to the tracker, and the observed recovery was worse than the original fault — a silent
revert to the previous status with no comment, indistinguishable from data loss to an owner
watching several items at once.

Linear has no API token in this repository, so the transition itself cannot be mechanised. What
can be mechanised is the judgment: this script collapses "is this item actually started?" into
one command with a binary answer, so the agent has nothing to assess and nothing to skip.

Usage:  venv/bin/python scripts/check_item_ready.py NAD-123
Exit 0 = worktree and one valid, unambiguous item contract are present; set ``In Progress``.
Exit 1 = not ready; fix the cause, or stop and move the issue to ``Blocked`` with a comment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.hotfix_contract import validate_hotfix_file


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


def worktree_branches(porcelain: str) -> set[str]:
    """Branch names registered as worktrees, from `git worktree list --porcelain`."""
    branches: set[str] = set()
    for line in porcelain.splitlines():
        if line.startswith("branch refs/heads/"):
            branches.add(line.removeprefix("branch refs/heads/"))
    return branches


def readiness_problems(
    item: str,
    branches: set[str],
    plan_exists: bool,
    current_branch: str,
    *,
    hotfix_exists: bool = False,
    hotfix_problems: list[str] | None = None,
) -> list[str]:
    """Return the reasons this item is not ready to be marked In Progress."""
    problems: list[str] = []
    expected = f"work/{item}"

    if expected not in branches:
        problems.append(
            f"no worktree is registered for branch '{expected}' — "
            f"run: bash scripts/worktree-new.sh {item}"
        )
    if current_branch != expected:
        problems.append(
            f"you are on '{current_branch}', not inside the item's worktree — "
            f"run: cd .codex/worktrees/{item.lower()}"
        )
    if hotfix_exists:
        if plan_exists:
            problems.append(
                "hotfix.md cannot coexist with plan.md; resolve the ambiguous item contract."
            )
        if hotfix_problems:
            problems.extend(hotfix_problems)
    elif not plan_exists:
        problems.append(
            f"docs/work/{item}/plan.md is not present in this working tree — "
            "planning never committed and pushed the approved plan or valid hotfix contract"
        )
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_item_ready.py <ITEM-ID>", file=sys.stderr)
        return 2

    item = sys.argv[1]
    branches = worktree_branches(_git("worktree", "list", "--porcelain"))
    current_branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    root = Path.cwd()
    plan_exists = (root / "docs/work" / item / "plan.md").is_file()
    hotfix_path = root / "docs/work" / item / "hotfix.md"
    hotfix_exists = hotfix_path.exists()
    hotfix_problems = validate_hotfix_file(root, hotfix_path, item) if hotfix_exists else None

    problems = readiness_problems(
        item,
        branches,
        plan_exists,
        current_branch,
        hotfix_exists=hotfix_exists,
        hotfix_problems=hotfix_problems,
    )
    if not problems:
        contract = "hotfix contract" if hotfix_exists else "plan"
        print(f"{item} is ready: you are in its worktree and the {contract} is present.")
        return 0

    print(f"{item} is NOT ready to be marked In Progress:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nDo not set In Progress. Fix the cause above, or stop and move the issue to Blocked\n"
        "with a one-line comment naming the blocker. Never set a status and revert it later.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
