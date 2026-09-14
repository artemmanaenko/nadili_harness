#!/usr/bin/env bash
# Fail before a role turn when a worktree cannot execute the repository's fixed gates.
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/.nvmrc")"
PACKAGE_MANAGER="$(sed -n 's/.*"packageManager": "\([^"]*\)".*/\1/p' "$PROJECT_ROOT/package.json")"
PNPM_VERSION="${PACKAGE_MANAGER#pnpm@}"

fail() {
    printf 'Worktree preflight failed: %s\n' "$1" >&2
    printf "Run 'bash scripts/worktree-setup.sh' from this worktree, then retry.\n" >&2
    exit 2
}

[[ "$(node --version 2>/dev/null || true)" == "v$NODE_VERSION" ]] || fail "Node $NODE_VERSION is not active."
[[ "$(pnpm --version 2>/dev/null || true)" == "$PNPM_VERSION" ]] || fail "pnpm $PNPM_VERSION is not active."
[[ -x "$PROJECT_ROOT/venv/bin/python" ]] || fail "venv/bin/python is missing."
[[ -d "$PROJECT_ROOT/node_modules/.pnpm" ]] || fail "frozen workspace dependencies are missing."

if ! "$PROJECT_ROOT/venv/bin/python" -c "import fastapi, sqlalchemy; import apps.api.db"; then
    fail "the Python backend runtime is incomplete."
fi

if ! pnpm --dir "$PROJECT_ROOT/apps/admin-web" exec node --input-type=module -e '
import { chromium } from "@playwright/test";
const browser = await chromium.launch({ headless: true });
await browser.close();
'; then
    fail "the Chromium screenshot runtime cannot launch."
fi

printf 'Worktree preflight passed.\n'
