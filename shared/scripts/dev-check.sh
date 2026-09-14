#!/usr/bin/env bash
# Affected development integration check. The full release gate remains scripts/pre-release.sh.
set -euo pipefail
umask 077

if [[ "$#" -ne 0 ]]; then
  printf 'Usage: bash scripts/dev-check.sh\n' >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# shellcheck source=/dev/null
source "$root/scripts/lib/gate-slots.sh"
nadili_gate_run_register
trap nadili_gate_run_unregister EXIT

printf '== dev-check: affected dirty-scope checks, NOT release qualification.\n'
printf '== Before releasing: bash scripts/pre-release.sh\n'

# The integration runner owns dirty-scope calculation, prints capability reasons, and executes
# only selected capabilities. Full pre-commit and pre-release profiles remain separate.
# shellcheck source=/dev/null
source "$root/scripts/integration-check.sh"
run_integration_check --dev
