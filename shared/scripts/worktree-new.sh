#!/usr/bin/env bash
# Create or resume one parallel work-item worktree with a working toolchain, in one
# deterministic step.
#
# This is the supported way to start a worktree for Nadili items and `$start-work-item` /
# `$manage-work-item` parallel sessions. It shares the primary checkout's Python venv by
# symlink (every work item uses the same locked backend dependencies, so a second real venv
# is wasted disk and a second place to go stale or, worse, exist-but-be-empty) and installs a
# real per-worktree `node_modules` via pnpm (JS deps do need a real install; they contain
# native/path-sensitive artifacts). `scripts/worktree-setup.sh` remains for the rarer case of
# a genuinely fresh clone with no primary venv yet to link to.
#
# Idempotent: if `work/<ID>` already exists on origin (e.g. a prior Nadili planning session
# already pushed a plan commit to it), this resumes that branch at its current tip instead of
# failing on "branch already exists" — a fresh planning session and a later implementation
# session for the same item both call this exact command.
set -euo pipefail
umask 077

if [[ $# -lt 1 ]]; then
    echo "Usage: bash scripts/worktree-new.sh <WORK-ITEM-ID> [base-worktree-path]" >&2
    exit 2
fi

WORK_ITEM_ID="$1"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_ITEM_ID_LOWER="$(printf '%s' "$WORK_ITEM_ID" | tr '[:upper:]' '[:lower:]')"
TARGET_DIR="${2:-$PROJECT_ROOT/.codex/worktrees/$WORK_ITEM_ID_LOWER}"
BRANCH="work/$WORK_ITEM_ID"

if [[ ! -x "$PROJECT_ROOT/venv/bin/python" ]]; then
    echo "Primary venv is missing at $PROJECT_ROOT/venv; run 'bash setup.sh' there first." >&2
    exit 2
fi

git -C "$PROJECT_ROOT" fetch origin --quiet

if git -C "$PROJECT_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    echo "→ Branch $BRANCH already exists on origin — resuming it at $TARGET_DIR ..."
    git -C "$PROJECT_ROOT" worktree add "$TARGET_DIR" -B "$BRANCH" "origin/$BRANCH"
else
    echo "→ Adding worktree at $TARGET_DIR on new branch $BRANCH ..."
    git -C "$PROJECT_ROOT" worktree add "$TARGET_DIR" -b "$BRANCH" origin/main
fi

# `umask 077` above (deliberate: this script writes env files) also applies to git's checkout, so
# a tracked 100755 file lands as 0700 instead of 0755. Git records only the executable bit, so it
# reports no modification and the drift is invisible -- but tests that assert an exact mode fail in
# every worktree while passing in the primary checkout. Restore the tracked modes explicitly rather
# than relaxing the umask, which would also loosen the env files this script creates.
echo "→ Restoring tracked file modes clamped by umask ..."
git -C "$TARGET_DIR" ls-files --stage -z \
    | while IFS= read -r -d "" entry; do
        mode="${entry%% *}"
        path="${entry#*$'\t'}"
        case "$mode" in
            100755) chmod 755 "$TARGET_DIR/$path" ;;
            100644) chmod 644 "$TARGET_DIR/$path" ;;
        esac
    done

echo "→ Linking the primary venv (shared, not copied) ..."
ln -s "$PROJECT_ROOT/venv" "$TARGET_DIR/venv"

echo "→ Installing frozen JS workspace dependencies ..."
(cd "$TARGET_DIR" && pnpm install --frozen-lockfile)

echo "→ Installing the pinned Chromium screenshot runtime ..."
pnpm --dir "$TARGET_DIR/apps/admin-web" exec playwright install chromium

echo "→ Verifying the linked venv has backend dependencies ..."
if ! "$TARGET_DIR/venv/bin/python" -c "import fastapi, sqlalchemy; import apps.api.db" 2>/dev/null; then
    echo "Linked venv failed a real import smoke check; do not proceed on this worktree." >&2
    exit 1
fi

echo "→ Running the mandatory worktree preflight ..."
bash "$TARGET_DIR/scripts/worktree-preflight.sh"

echo "✓ Worktree ready: $TARGET_DIR ($BRANCH)"
