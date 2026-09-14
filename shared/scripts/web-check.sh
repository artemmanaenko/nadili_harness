#!/usr/bin/env bash
# Web quality gate. Fast covers static checks and unit tests; full also builds both apps.
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"
scope="${2:-all}"

if [[ "$mode" != "fast" && "$mode" != "full" ]]; then
  printf 'Usage: scripts/web-check.sh <fast|full> [all|admin|public]\n' >&2
  exit 2
fi
if [[ "$scope" != "all" && "$scope" != "admin" && "$scope" != "public" ]]; then
  printf 'Usage: scripts/web-check.sh <fast|full> [all|admin|public]\n' >&2
  exit 2
fi

# pnpm may need to reconcile node_modules after another worktree updates the shared lockfile.
# In a non-interactive agent/CI run it must never stop to ask whether that purge is allowed.
if [[ ! -t 0 && -z "${CI:-}" ]]; then
  export CI=true
fi

cd "$root"
pnpm_command=(corepack pnpm)

# shellcheck source=lib/gate-slots.sh
source "$root/scripts/lib/gate-slots.sh"
nadili_gate_run_register
trap 'nadili_gate_release; nadili_gate_run_unregister' EXIT

# A full gate's static/unit checks run in this ordinary phase. The supervised child is a private
# build-only phase: the marker is never sufficient by itself, because only the gate executor's
# inherited open descriptor and holder token can authorize skipping the checks.
private_build_phase=false
private_build_phase_invalid=false
supervised_child_complete=false
if [[ "$mode" == "full" && -n "${NADILI_GATE_PRIVATE_BUILD_PHASE:-}" ]]; then
  if [[ "${NADILI_GATE_PRIVATE_BUILD_PHASE}" == 1 ]] && nadili_gate_inherited_lease; then
    private_build_phase=true
  else
    private_build_phase_invalid=true
    printf 'web-check: private build phase requires a valid inherited heavy lease\n' >&2
  fi
fi

run() {
  local label="$1"
  printf '[run] %s\n' "$label"
  shift
  "$@"
  printf '[ok] %s\n' "$label"
}

# client-ts and the shared UI are consumed by both apps, so either isolated app scope still runs
# those shared checks. App-specific checks are selected only after the shared contract surface.
if [[ "$private_build_phase" == false ]]; then
  run "Client contract" "${pnpm_command[@]}" --filter @nadili/client-ts check
  run "Client typecheck" "${pnpm_command[@]}" --filter @nadili/client-ts typecheck
  run "Client tests" "${pnpm_command[@]}" --filter @nadili/client-ts test
  run "Shared UI typecheck" "${pnpm_command[@]}" --filter @nadili/ui typecheck
  run "Shared UI tests" "${pnpm_command[@]}" --filter @nadili/ui test
  if [[ "$scope" == "all" || "$scope" == "public" ]]; then
    run "Public web lint" "${pnpm_command[@]}" --filter @nadili/web lint
    run "Public web typecheck" "${pnpm_command[@]}" --filter @nadili/web typecheck
    run "Public web tests" "${pnpm_command[@]}" --filter @nadili/web test
  fi
  if [[ "$scope" == "all" || "$scope" == "admin" ]]; then
    run "Admin web lint" "${pnpm_command[@]}" --filter @nadili/admin-web lint
    run "Admin web typecheck" "${pnpm_command[@]}" --filter @nadili/admin-web typecheck
    run "Admin web tests" "${pnpm_command[@]}" --filter @nadili/admin-web test
  fi
fi

if [[ "$mode" == "full" ]]; then
  if [[ "$private_build_phase_invalid" == true ]]; then
    exit 2
  fi
  if [[ "$private_build_phase" == false ]] && ! nadili_gate_inherited_lease; then
    NADILI_GATE_PRIVATE_BUILD_PHASE=1 "$(nadili_gate_python)" "$root/scripts/gate_executor.py" run \
      --lock-root "$(nadili_gate_lock_root)" --label web-check-full -- \
      /bin/bash "$0" "$@"
    supervised_child_complete=true
  fi
fi

if [[ "$mode" == "full" && "$supervised_child_complete" == false ]]; then
  next_cpus="$(nadili_gate_next_cpus)"
  build_env=(
    env
    -u NADILI_GATE_NEXT_CPUS
    "NADILI_ENVIRONMENT=local"
    "NADILI_API_BASE_URL=${NADILI_API_BASE_URL:-https://api.example.test}"
    "NADILI_SITE_URL=${NADILI_SITE_URL:-https://nadili.example.test}"
    "NADILI_TURNSTILE_SITE_KEY=1x00000000000000000000AA"
    "NADILI_TURNSTILE_TEST_MODE=true"
    "NADILI_REVALIDATE_SECRET=${NADILI_REVALIDATE_SECRET:-ci-revalidate-secret-not-for-production}"
  )
  if [[ -n "$next_cpus" ]]; then
    build_env+=("NADILI_GATE_NEXT_CPUS=$next_cpus")
  fi
  nice_prefix_value="$(nadili_gate_nice_prefix)"
  nice_prefix=()
  if [[ -n "$nice_prefix_value" ]]; then
    read -r -a nice_prefix <<< "$nice_prefix_value"
  fi
  # Production builds are the heaviest step here (each spawns its own
  # per-core workers). Keep both builds under the same shared lease; nested
  # calls reuse the verified inherited descriptor.
  build_slots="${NADILI_GATE_BUILD_SLOTS:-1}"
  nadili_gate_acquire nadili-web-build "$build_slots"
  if [[ "$scope" == "all" || "$scope" == "public" ]]; then
    build_command=("${build_env[@]}" "${pnpm_command[@]}" --filter @nadili/web build)
    if [[ -n "$nice_prefix_value" ]]; then
      build_command=("${nice_prefix[@]}" "${build_command[@]}")
    fi
    run "Public web build" "${build_command[@]}"
  fi
  if [[ "$scope" == "all" || "$scope" == "admin" ]]; then
    build_command=("${build_env[@]}" "${pnpm_command[@]}" --filter @nadili/admin-web build)
    if [[ -n "$nice_prefix_value" ]]; then
      build_command=("${nice_prefix[@]}" "${build_command[@]}")
    fi
    run "Admin web build" "${build_command[@]}"
  fi
  nadili_gate_release
fi

printf 'Web %s gate passed.\n' "$mode"
