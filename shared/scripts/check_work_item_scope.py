#!/usr/bin/env python3
"""Refuse a commit that does not belong to exactly one work item.

Several agents share this machine and the primary checkout, so the recurring damage is a commit
picking up work that is not its own — typically via ``git add -A``, which sweeps a neighbour's
untracked plan or pending deletions into an unrelated commit. Prose rules did not prevent this;
these checks make the bad commit fail instead.

Three rules, all derived from observed incidents on this repo:

* a plan or hotfix contract under ``docs/work/<ID>/`` may only be committed on that item's
  branch, ``work/<ID>``;
* one commit may not stage files belonging to two different work items;
* while on ``work/<ID>``, no other item's ``docs/work/<OTHER>/`` files may be staged.

Lives in ``scripts/`` on purpose: ``TRIP-upgrade`` rewrites the TRIP skill files from the vendor
template, so a rule that only exists there is lost on the next upgrade. This one is not.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

WORK_PATH_RE = re.compile(r"^docs/work/(?P<item>[^/]+)/")
CONTRACT_PATH_RE = re.compile(r"^docs/work/(?P<item>[^/]+)/(?:plan|hotfix)\.md$")
BRANCH_ITEM_RE = re.compile(r"^work/(?P<item>.+)$")


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
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    return [line for line in out.splitlines() if line]


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def _branch_item(branch: str) -> str | None:
    match = BRANCH_ITEM_RE.match(branch)
    return match.group("item") if match else None


def violations(staged: list[str], branch: str) -> list[str]:
    """Return one message per rule broken by this staged change set."""
    problems: list[str] = []
    branch_item = _branch_item(branch)

    for path in staged:
        match = CONTRACT_PATH_RE.match(path)
        if match is None:
            continue
        item = match.group("item")
        if branch != f"work/{item}":
            problems.append(
                f"{path} may only be committed on branch 'work/{item}', but HEAD is '{branch}'."
            )

    touched: dict[str, str] = {}
    for path in staged:
        match = WORK_PATH_RE.match(path)
        if match is None:
            continue
        touched.setdefault(match.group("item"), path)

    if len(touched) > 1:
        listed = ", ".join(f"{item} ({path})" for item, path in sorted(touched.items()))
        problems.append(
            f"one commit stages files for {len(touched)} work items: {listed}. "
            "Split it — a commit belongs to exactly one item."
        )

    if branch_item is not None:
        foreign = sorted(item for item in touched if item != branch_item)
        for item in foreign:
            problems.append(
                f"{touched[item]} belongs to {item}, but this branch is for {branch_item}."
            )

    return problems


def main() -> int:
    staged = _staged_paths()
    if not staged:
        return 0

    problems = violations(staged, _current_branch())
    if not problems:
        return 0

    print("Commit does not belong to exactly one work item:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nStage explicit paths for your item only — never `git add -A` in a shared checkout.\n"
        "Work on an item happens in its own worktree:\n"
        "  bash scripts/worktree-new.sh <ID>   # idempotent: creates or resumes\n"
        "  cd .codex/worktrees/<id-lower>\n"
        "To drop someone else's file from the index without deleting it:\n"
        "  git restore --staged <path>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
