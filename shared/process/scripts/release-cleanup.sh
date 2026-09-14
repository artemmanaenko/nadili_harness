#!/usr/bin/env bash
# Safely reconcile the primary checkout and remove one delivered TRIP worktree.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: bash release-cleanup.sh <primary-repo-root> <worktree-path> <WORK-ITEM-ID>" >&2
    exit 2
fi

PRIMARY_ROOT="$(cd "$1" && pwd -P)"
WORKTREE_PATH="$(cd "$2" && pwd -P)"
WORK_ITEM_ID="$3"
BRANCH="work/$WORK_ITEM_ID"

if [[ ! "$WORK_ITEM_ID" =~ ^[A-Z][A-Z0-9]*-[0-9]+$ ]]; then
    echo "Invalid work item id: $WORK_ITEM_ID" >&2
    exit 2
fi

if [[ "$(git -C "$PRIMARY_ROOT" rev-parse --show-toplevel)" != "$PRIMARY_ROOT" ]]; then
    echo "Primary path is not the repository root: $PRIMARY_ROOT" >&2
    exit 2
fi

case "$WORKTREE_PATH" in
    "$PRIMARY_ROOT"/.codex/worktrees/*) ;;
    *)
        echo "Refusing worktree outside $PRIMARY_ROOT/.codex/worktrees: $WORKTREE_PATH" >&2
        exit 2
        ;;
esac

if [[ "$(git -C "$PRIMARY_ROOT" branch --show-current)" != "main" ]]; then
    echo "Primary checkout is not on main; refusing cleanup." >&2
    exit 1
fi

if [[ "$(git -C "$WORKTREE_PATH" branch --show-current)" != "$BRANCH" ]]; then
    echo "Worktree is not on expected branch $BRANCH; refusing cleanup." >&2
    exit 1
fi

if [[ -n "$(git -C "$WORKTREE_PATH" status --porcelain --untracked-files=normal)" ]]; then
    echo "Worktree has uncommitted or untracked files; inspect it before cleanup." >&2
    exit 1
fi

if ! git -C "$PRIMARY_ROOT" diff --quiet || ! git -C "$PRIMARY_ROOT" diff --cached --quiet; then
    echo "Primary checkout has tracked changes; refusing to move main." >&2
    exit 1
fi

git -C "$PRIMARY_ROOT" fetch origin --quiet

if ! git -C "$PRIMARY_ROOT" merge-base --is-ancestor "$BRANCH" origin/main; then
    echo "$BRANCH is not contained in origin/main; refusing cleanup." >&2
    exit 1
fi

if ! git -C "$PRIMARY_ROOT" merge-base --is-ancestor main origin/main; then
    echo "Local main cannot fast-forward to origin/main; refusing cleanup." >&2
    exit 1
fi

git -C "$PRIMARY_ROOT" merge --ff-only origin/main
git -C "$PRIMARY_ROOT" worktree remove "$WORKTREE_PATH"

if git -C "$PRIMARY_ROOT" rev-parse --verify --quiet "${BRANCH}@{upstream}" >/dev/null; then
    git -C "$PRIMARY_ROOT" branch --unset-upstream "$BRANCH"
fi

git -C "$PRIMARY_ROOT" branch -d "$BRANCH"
git -C "$PRIMARY_ROOT" worktree prune

echo "Released worktree removed: $WORKTREE_PATH ($BRANCH)"
