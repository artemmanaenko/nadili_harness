#!/usr/bin/env bash
# Run the conservative affected integration capability union.
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$root/venv/bin/python"

usage() {
  printf 'Usage: bash scripts/integration-check.sh (--staged|--dirty|--base BASE [--head HEAD])\n'
  printf '       bash scripts/integration-check.sh --dev\n' >&2
}

run_integration_check() {
  local scope=() dry_run=false base="" head="HEAD" argument
  while (($# > 0)); do
    argument="$1"
    shift
    case "$argument" in
      --staged|--dirty)
        if ((${#scope[@]} > 0)); then
          printf 'integration-check: choose one change scope\n' >&2
          return 2
        fi
        scope=("$argument")
        ;;
      --base)
        if (($# == 0)) || ((${#scope[@]} > 0)); then
          printf 'integration-check: --base requires the only change scope and a value\n' >&2
          return 2
        fi
        base="$1"
        shift
        scope=(--base "$base")
        ;;
      --head)
        if (($# == 0)); then
          printf 'integration-check: --head requires a value\n' >&2
          return 2
        fi
        head="$1"
        shift
        ;;
      --dev)
        scope=(--dirty)
        ;;
      --dry-run)
        dry_run=true
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        printf 'integration-check: unknown argument %s\n' "$argument" >&2
        usage
        return 2
        ;;
    esac
  done

  if ((${#scope[@]} == 0)); then
    printf 'integration-check: a complete scope is required\n' >&2
    usage
    return 2
  fi
  local verified_base="" verified_head="" verified_tree=""
  if [[ "${scope[0]}" == "--base" ]]; then
    if ! verified_base=$(git rev-parse --verify "${base}^{commit}") || \
       ! verified_head=$(git rev-parse --verify "${head}^{commit}"); then
      printf 'integration-check: base/head must resolve to commits\n' >&2
      return 1
    fi
    scope=(--base "$verified_base" --head "$verified_head")
    if [[ "$dry_run" == false ]]; then
      "$python_bin" "$root/scripts/gate_evidence.py" snapshot --head "$verified_head"
    fi
  elif [[ "${scope[0]}" == "--staged" && "$dry_run" == false ]]; then
    verified_tree=$("$python_bin" "$root/scripts/gate_evidence.py" snapshot --print-tree)
  fi

  local selector_args=("--integration" "${scope[@]}")
  local details capabilities_output admin_specs_output paths_output web_scope
  if ! details=$("$python_bin" "$root/scripts/gate_lanes.py" "${selector_args[@]}"); then
    printf '%s\n' "$details" >&2
    return 1
  fi
  capabilities_output=$("$python_bin" "$root/scripts/gate_lanes.py" \
    "${selector_args[@]}" --capabilities-only)
  admin_specs_output=$("$python_bin" "$root/scripts/gate_lanes.py" \
    "${selector_args[@]}" --admin-specs-only)
  paths_output=$("$python_bin" "$root/scripts/gate_lanes.py" \
    "${selector_args[@]}" --paths-only)
  web_scope=$("$python_bin" "$root/scripts/gate_lanes.py" \
    "${selector_args[@]}" --web-scope-only)

  # Expansions of these use the ${arr[@]+...} form below: /bin/bash on macOS is 3.2, where
  # "${arr[@]}" on an empty array is an unbound-variable error under `set -u`.
  local -a capabilities=() admin_specs=() resolved_paths=()
  while IFS= read -r capability; do
    [[ -n "$capability" ]] && capabilities+=("$capability")
  done <<< "$capabilities_output"
  while IFS= read -r spec; do
    [[ -n "$spec" ]] && admin_specs+=("$spec")
  done <<< "$admin_specs_output"
  while IFS= read -r path; do
    [[ -n "$path" ]] && resolved_paths+=("$path")
  done <<< "$paths_output"

  printf 'integration scope: %s\n' "${scope[*]}"
  printf '%s\n' "$details"

  if [[ "${scope[0]}" == "--base" ]]; then
    for capability in ${capabilities[@]+"${capabilities[@]}"}; do
      case "$capability" in
        full-baseline|admin-e2e|public-visual|portability)
          printf 'integration-check: range scope selected a heavy capability\n' >&2
          return 1
          ;;
      esac
    done
  fi

  if [[ "$dry_run" == true ]]; then
    while IFS= read -r capability; do
      [[ -n "$capability" ]] && printf 'would-run: %s\n' "$capability"
    done <<< "$capabilities_output"
    for spec in ${admin_specs[@]+"${admin_specs[@]}"}; do
      printf 'would-run-admin-spec: %s\n' "$spec"
    done
    for path in ${resolved_paths[@]+"${resolved_paths[@]}"}; do
      printf 'would-run-path: %s\n' "$path"
    done
    return 0
  fi

  local -a document_command=()
  if ((${#resolved_paths[@]} > 0)); then
    document_command=("$python_bin" "$root/scripts/check_staged_documents.py")
    for path in ${resolved_paths[@]+"${resolved_paths[@]}"}; do
      document_command+=(--path "$path")
    done
    # Document validation is independent of capability selection: a control-plane path may force
    # full-baseline while still carrying a document that must be checked.
    "${document_command[@]}"
  fi

  local cheap_done=false web_build_selected=false web_build_done=false
  local capability selected_capability spec
  local -a cheap_command=() admin_command=()
  # gate_lanes.py is the single ordering authority: deterministic client, contract and web
  # checks must fail before the disposable PostgreSQL integration entrypoint.
  for capability in ${capabilities[@]+"${capabilities[@]}"}; do
    [[ "$capability" == web-build ]] && web_build_selected=true
  done
  for capability in ${capabilities[@]+"${capabilities[@]}"}; do
    case "$capability" in
      full-baseline)
        bash scripts/pre-commit.sh
        bash scripts/backend.sh check-postgres
        bash scripts/admin-web-e2e.sh
        bash scripts/runtime-liveness-smoke.sh
        bash scripts/public-web-visual.sh
        bash scripts/portability-smoke.sh
        ;;
      gate-tooling|python-static|python-unit|web-static|web-unit|harness)
        if [[ "$cheap_done" == false ]]; then
          cheap_command=(bash scripts/pre-commit-fast.sh --integration --web-scope "$web_scope")
          for selected_capability in ${capabilities[@]+"${capabilities[@]}"}; do
            case "$selected_capability" in
              gate-tooling|python-static|python-unit|harness)
                cheap_command+=(--capability "$selected_capability")
                ;;
              web-static|web-unit)
                if [[ "$web_build_selected" == false ]]; then
                  cheap_command+=(--capability "$selected_capability")
                fi
                ;;
              web-build)
                cheap_command+=(--capability web-build)
                ;;
            esac
          done
          if ((${#cheap_command[@]} > 2)); then
            "${cheap_command[@]}"
            [[ "$web_build_selected" == true ]] && web_build_done=true
          fi
          cheap_done=true
        fi
        ;;
      backend-integration)
        bash scripts/backend.sh check-postgres
        ;;
      client-python)
        bash scripts/client.sh
        ;;
      contracts)
        "$python_bin" "$root/scripts/export_openapi.py" --check
        "$python_bin" "$root/scripts/check_openapi_contract.py"
        ;;
      web-build)
        if [[ "$web_build_done" == false ]]; then
          bash scripts/web-check.sh full "$web_scope"
          web_build_done=true
        fi
        ;;
      admin-e2e)
        admin_command=(bash scripts/admin-web-e2e.sh)
        if ((${#admin_specs[@]} > 0)); then
          admin_command+=(--)
          admin_command+=(${admin_specs[@]+"${admin_specs[@]}"})
        fi
        "${admin_command[@]}"
        ;;
      public-visual)
        bash scripts/public-web-visual.sh
        ;;
      portability)
        bash scripts/portability-smoke.sh
        ;;
      docs)
        :
        ;;
      *)
        printf 'integration-check: selector emitted unknown capability %s\n' "$capability" >&2
        return 1
        ;;
    esac
  done
  if [[ -n "$verified_head" ]]; then
    "$python_bin" "$root/scripts/gate_evidence.py" snapshot --head "$verified_head"
  elif [[ "${scope[0]}" == "--staged" && "$dry_run" == false ]]; then
    "$python_bin" "$root/scripts/gate_evidence.py" snapshot --tree "$verified_tree"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  cd "$root"
  run_integration_check "$@"
fi
