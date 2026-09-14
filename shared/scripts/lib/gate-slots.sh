# Shared machine-wide heavy lease plus the gate-run CPU census.
# serialized by an OS-backed lock; cheap static/unit work still uses the census
# below to derive worker budgets and remains concurrent.
#
# Design constraints, learned the hard way:
# - One lease covers every heavy phase, including Docker, browser, PostgreSQL,
#   standalone builds, and release qualification. It never fails open.
# - The lease is inherited by descriptor, token, and holder identity. An
#   environment marker without the open lock descriptor is not authority.
# - The lease owner supervises a process group and releases only after the
#   caller's resource cleanup has completed.
# - Anything that runs the gate scripts under a test harness must set
#   NADILI_GATE_LOCK_DIR to a private directory; otherwise the test run
#   would consume -- or worse, wait on -- the machine's real lease.
# - Gate runs register a PID marker in gate-runs/ for their whole lifetime.
#   This registry is a non-blocking, fail-open census used to derive the
#   pytest and Vitest worker budgets across the full gate, including periods
#   when the run holds no phase slot. Counting held slots was rejected as the
#   sibling signal because slots are intentionally held only during short
#   resource spikes, so a sibling in a test phase would otherwise be invisible.
# - Derived worker counts use min(census, CPU-share budget); explicit worker
#   overrides remain verbatim. Throttle diagnostics name the binding constraint
#   so a gate's reduced worker count is explainable from stderr alone.
# - The CPU-share budget only binds while at least one live sibling gate run
#   is registered. With zero live siblings there is nothing to share the host
#   with, so the budget is unset (same opt-out as an explicit share >= 100)
#   and derivation falls back to the census alone -- all cores on an idle
#   host. An explicit NADILI_GATE_CPU_SHARE is honoured whenever siblings are
#   live; explicit NADILI_PYTEST_JOBS/NADILI_VITEST_WORKERS/
#   NADILI_GATE_NEXT_CPUS overrides always win over any derivation.
#
# Usage:
#   source "$root/scripts/lib/gate-slots.sh"
#   nadili_gate_acquire <pool> <max-slots>
#   ...heavy work...
#   nadili_gate_release
# `nadili_gate_slot_dir` tracks whether this shell has a lease (empty when none
# is held; inherited ownership is represented separately) so
# `nadili_gate_release` is safe to call unconditionally, including from an EXIT
# trap. Lease acquisition never fails open.
#
# Gate-run usage:
#   nadili_gate_run_register
#   trap nadili_gate_run_unregister EXIT
#   nadili_gate_pytest_jobs
#   nadili_gate_vitest_workers
#   nadili_gate_cpu_budget
#   nadili_gate_next_cpus
#   nadili_gate_nice_prefix
# The registry is read-only for callers: stale markers are reaped by atomic
# rename, and all filesystem failures fail open. An inherited
# NADILI_GATE_RUN_ID makes registration and unregistration no-ops for nested
# gates.

nadili_gate_slot_dir=""
nadili_gate_run_dir=""
nadili_gate_shell_lease_owned=""

nadili_gate_lock_root() {
  printf '%s\n' "${NADILI_GATE_LOCK_DIR:-${TMPDIR:-/tmp}/nadili-gate-locks}"
}

nadili_gate_executor_path() {
  local library_root
  library_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  printf '%s\n' "$library_root/scripts/gate_executor.py"
}

nadili_gate_python() {
  local library_root
  library_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  if [[ -x "$library_root/venv/bin/python" ]]; then
    printf '%s\n' "$library_root/venv/bin/python"
  else
    printf '%s\n' python3
  fi
}

nadili_gate_inherited_lease() {
  local lock_root
  lock_root="$(nadili_gate_lock_root)"
  [[ -n "${NADILI_GATE_LEASE_FD:-}" && -n "${NADILI_GATE_LEASE_TOKEN:-}" ]] || return 1
  "$(nadili_gate_python)" "$(nadili_gate_executor_path)" verify-inherited \
    --lock-root "$lock_root" >/dev/null 2>&1
}

nadili_gate_reexec_under_lease() {
  local root="$1" label="$2" script="$3"
  shift 3
  if nadili_gate_inherited_lease; then
    return 0
  fi
  exec "$(nadili_gate_python)" "$root/scripts/gate_executor.py" run \
    --lock-root "$(nadili_gate_lock_root)" --label "$label" -- \
    /bin/bash "$script" "$@"
}

nadili_gate_acquire() {
  local pool="$1" _max_slots="$2"
  local lock_root executor python wait_limit token
  lock_root="$(nadili_gate_lock_root)"
  executor="$(nadili_gate_executor_path)"
  python="$(nadili_gate_python)"
  wait_limit="${NADILI_GATE_SLOT_WAIT_LIMIT:-900}"

  # Existing call sites pass a phase-pool name and capacity. Keep that shell
  # interface stable while making both arguments diagnostic-only: there is one
  # machine-wide heavy lease now.
  if nadili_gate_inherited_lease; then
    nadili_gate_slot_dir="inherited"
    return 0
  fi

  mkdir -p -m 700 "$lock_root"
  exec 9<> "$lock_root/heavy.lock"
  chmod 600 "$lock_root/heavy.lock"
  if ! token="$( "$python" "$executor" acquire-fd \
    --lock-root "$lock_root" --fd 9 --label "$pool" --owner-pid "$$" \
    --timeout "$wait_limit")"; then
    exec 9>&-
    return 1
  fi
  export NADILI_GATE_LEASE_FD=9
  export NADILI_GATE_LEASE_ROOT="$lock_root"
  export NADILI_GATE_LEASE_TOKEN="$token"
  export NADILI_GATE_LEASE_OWNER=1
  nadili_gate_slot_dir="$lock_root/heavy.lock"
  nadili_gate_shell_lease_owned=1
}

nadili_gate_run_register() {
  if [[ -n "${NADILI_GATE_RUN_ID:-}" ]]; then
    return 0
  fi

  local run_root run_dir
  run_root="$(nadili_gate_lock_root)/gate-runs"
  run_dir="$run_root/$$"
  nadili_gate_run_dir=""

  if ! mkdir -p -m 700 "$run_root" 2>/dev/null; then
    printf 'gate-slots: unable to create gate-run registry; continuing without registration\n' >&2
    return 0
  fi
  if ! mkdir "$run_dir" 2>/dev/null; then
    printf 'gate-slots: unable to create gate-run marker; continuing without registration\n' >&2
    return 0
  fi
  if ! printf '%s\n' "$$" > "$run_dir/pid"; then
    printf 'gate-slots: unable to write gate-run marker; continuing without registration\n' >&2
    rm -rf "$run_dir" 2>/dev/null || true
    return 0
  fi

  nadili_gate_run_dir="$run_dir"
  export NADILI_GATE_RUN_ID="$$"
}

nadili_gate_run_unregister() {
  if [[ -n "$nadili_gate_run_dir" ]]; then
    if ! rm -rf "$nadili_gate_run_dir" 2>/dev/null; then
      printf 'gate-slots: unable to remove gate-run marker; continuing\n' >&2
    fi
  fi
  nadili_gate_run_dir=""
}

nadili_gate_live_runs() {
  local run_root run_dir owner_pid reap count=0 own_run_dir=""
  run_root="$(nadili_gate_lock_root)/gate-runs"
  if [[ ! -d "$run_root" ]]; then
    printf '0\n'
    return 0
  fi

  if [[ -n "${NADILI_GATE_RUN_ID:-}" ]]; then
    own_run_dir="$run_root/$NADILI_GATE_RUN_ID"
  fi
  for run_dir in "$run_root"/*; do
    [[ -d "$run_dir" ]] || continue
    [[ "$run_dir" == "$nadili_gate_run_dir" || "$run_dir" == "$own_run_dir" ]] && continue
    if ! owner_pid="$(cat "$run_dir/pid" 2>/dev/null)" || [[ -z "$owner_pid" ]]; then
      continue
    fi
    if kill -0 "$owner_pid" 2>/dev/null; then
      count=$((count + 1))
      continue
    fi
    reap="$run_dir.reap.$$"
    if mv "$run_dir" "$reap" 2>/dev/null; then
      rm -rf "$reap" 2>/dev/null || true
    fi
  done
  printf '%s\n' "$count"
}

nadili_gate_cpu_count() {
  local cores=""
  if command -v sysctl >/dev/null 2>&1; then
    cores="$(sysctl -n hw.ncpu 2>/dev/null || true)"
  fi
  if [[ -z "$cores" ]] && command -v nproc >/dev/null 2>&1; then
    cores="$(nproc 2>/dev/null || true)"
  fi
  printf '%s\n' "$cores"
}

nadili_gate_cpu_budget() {
  local cores raw_share share budget siblings
  cores="$(nadili_gate_cpu_count)"
  if [[ ! "$cores" =~ ^[0-9]+$ ]] || ((cores == 0)); then
    return 0
  fi

  # The share cap exists so CONCURRENT gates split the host. With no live
  # sibling gate run, there is nothing to share with, so an idle host gets no
  # cap at all -- same opt-out as an explicit share >= 100. With at least one
  # live sibling, the cap binds exactly as before, including an explicit
  # NADILI_GATE_CPU_SHARE override.
  siblings="$(nadili_gate_live_runs)"
  if [[ ! "$siblings" =~ ^[0-9]+$ ]]; then
    siblings=0
  fi
  if ((siblings == 0)); then
    return 0
  fi

  raw_share="${NADILI_GATE_CPU_SHARE:-60}"
  if [[ ! "$raw_share" =~ ^[0-9]+$ ]]; then
    printf 'gate-slots: invalid NADILI_GATE_CPU_SHARE; ignoring CPU share cap\n' >&2
    return 0
  fi

  share="$raw_share"
  while [[ "$share" == 0* && "$share" != "0" ]]; do
    share="${share#0}"
  done
  if [[ "$share" == "0" ]]; then
    printf 'gate-slots: invalid NADILI_GATE_CPU_SHARE; ignoring CPU share cap\n' >&2
    return 0
  fi
  # After leading-zero stripping, three or more digits is exactly "share >= 100",
  # which is the documented opt-out: no cap, and no warning, because asking for the
  # whole machine is a legitimate choice rather than a mistake. Compared by length
  # rather than value so an absurdly long digit string cannot overflow (( )).
  if ((${#share} >= 3)); then
    return 0
  fi

  budget=$((cores * share / 100))
  if ((budget < 1)); then
    budget=1
  fi
  printf '%s\n' "$budget"
}

# Emits the one diagnostic line that explains a reduced worker count. Naming the
# binding constraint is a design requirement of this file, not decoration: a gate
# that quietly runs at half parallelism must be explainable from its own stderr.
# Pass an empty budget when the census bound the count rather than the CPU share.
nadili_gate_throttle_notice() {
  local subject="$1" workers="$2" siblings="$3" cores="$4" budget="${5:-}"
  local sibling_label="siblings"
  if ((siblings == 1)); then
    sibling_label="sibling"
  fi
  if [[ -n "$budget" ]]; then
    printf 'gate-slots: throttling %s to %s (%s live %s, %s cores, share budget %s)\n' \
      "$subject" "$workers" "$siblings" "$sibling_label" "$cores" "$budget" >&2
  else
    printf 'gate-slots: throttling %s to %s (%s live %s, %s cores)\n' \
      "$subject" "$workers" "$siblings" "$sibling_label" "$cores" >&2
  fi
}

nadili_gate_pytest_jobs() {
  if [[ -n "${NADILI_PYTEST_JOBS:-}" ]]; then
    printf '%s\n' "$NADILI_PYTEST_JOBS"
    return 0
  fi

  local cores siblings workers budget share_bound=0
  cores="$(nadili_gate_cpu_count)"
  if [[ ! "$cores" =~ ^[0-9]+$ ]] || ((cores == 0)); then
    printf 'auto\n'
    return 0
  fi
  siblings="$(nadili_gate_live_runs)"
  if [[ ! "$siblings" =~ ^[0-9]+$ ]]; then
    siblings=0
  fi
  workers=$((cores / (siblings + 1)))
  if ((workers < 4)); then
    workers=4
  fi
  if ((workers > cores)); then
    workers="$cores"
  fi
  budget="$(nadili_gate_cpu_budget)"
  if [[ "$budget" =~ ^[0-9]+$ ]] && ((workers > budget)); then
    workers="$budget"
    share_bound=1
  fi
  if ((workers < 1)); then
    workers=1
  fi
  if ((workers < cores)); then
    if ((share_bound)); then
      nadili_gate_throttle_notice "pytest workers" "$workers" "$siblings" "$cores" "$budget"
    else
      nadili_gate_throttle_notice "pytest workers" "$workers" "$siblings" "$cores"
    fi
  fi
  printf '%s\n' "$workers"
}

# Vitest's default is cores - 1, so solo runs intentionally omit an override:
# emitting a derived number there would raise parallelism, not preserve it.
# The floor of 2 is deliberately conservative and NOT selected by measurement --
# NAD-189 measured down to 3 workers (two siblings on 10 cores) and never reached
# the floor, which only binds at four or more concurrent gates. Re-measure at that
# concurrency before lowering it. Evidence: docs/work/NAD-189/measurements.md.
nadili_gate_vitest_workers() {
  if [[ -n "${NADILI_VITEST_WORKERS:-}" ]]; then
    printf '%s\n' "$NADILI_VITEST_WORKERS"
    return 0
  fi

  local floor=2 cores siblings workers ceiling effective_floor budget share_bound=0
  cores="$(nadili_gate_cpu_count)"
  if [[ ! "$cores" =~ ^[0-9]+$ ]] || ((cores == 0)); then
    return 0
  fi
  siblings="$(nadili_gate_live_runs)"
  if [[ ! "$siblings" =~ ^[0-9]+$ ]]; then
    siblings=0
  fi
  ceiling=$((cores - 1))
  if ((ceiling < 1)); then
    ceiling=1
  fi
  budget="$(nadili_gate_cpu_budget)"
  if ((siblings == 0)); then
    if [[ "$budget" =~ ^[0-9]+$ ]] && ((budget < ceiling)); then
      nadili_gate_throttle_notice "vitest workers" "$budget" "$siblings" "$cores" "$budget"
      printf '%s\n' "$budget"
    fi
    return 0
  fi

  effective_floor=$((floor < ceiling ? floor : ceiling))
  workers=$((cores / (siblings + 1)))
  if ((workers < effective_floor)); then
    workers="$effective_floor"
  fi
  if ((workers > ceiling)); then
    workers="$ceiling"
  fi
  if [[ "$budget" =~ ^[0-9]+$ ]] && ((workers > budget)); then
    workers="$budget"
    share_bound=1
  fi
  if ((workers < 1)); then
    workers=1
  fi
  if ((share_bound)); then
    nadili_gate_throttle_notice "vitest workers" "$workers" "$siblings" "$cores" "$budget"
  else
    nadili_gate_throttle_notice "vitest workers" "$workers" "$siblings" "$cores"
  fi
  printf '%s\n' "$workers"
}

# Next's own default is `Math.max(1, cores - 1)` -- see the `cpus:` entry in
# next/dist/server/config-shared.js. The ticket claimed it was the full core count;
# it is not, and the difference matters: emitting `cores` here would set
# experimental.cpus ABOVE stock Next and *raise* build parallelism, which this file
# is never allowed to do. So the count is clamped to that ceiling, and when nothing
# binds below it the function emits nothing at all -- the call sites then export no
# variable and the build is byte-identical to an unmodified `next build`. Same shape
# as nadili_gate_vitest_workers, for the same reason.
nadili_gate_next_cpus() {
  local cores siblings workers budget ceiling share_bound=0
  cores="$(nadili_gate_cpu_count)"
  if [[ ! "$cores" =~ ^[0-9]+$ ]] || ((cores == 0)); then
    return 0
  fi
  siblings="$(nadili_gate_live_runs)"
  if [[ ! "$siblings" =~ ^[0-9]+$ ]]; then
    siblings=0
  fi

  ceiling=$((cores - 1))
  if ((ceiling < 1)); then
    ceiling=1
  fi

  workers=$((cores / (siblings + 1)))
  if ((workers < 1)); then
    workers=1
  fi
  budget="$(nadili_gate_cpu_budget)"
  if [[ "$budget" =~ ^[0-9]+$ ]] && ((workers > budget)); then
    workers="$budget"
    share_bound=1
  fi
  if ((workers < 1)); then
    workers=1
  fi
  if ((workers >= ceiling)); then
    return 0
  fi

  if ((workers < cores)); then
    if ((share_bound)); then
      nadili_gate_throttle_notice "Next build CPUs" "$workers" "$siblings" "$cores" "$budget"
    else
      nadili_gate_throttle_notice "Next build CPUs" "$workers" "$siblings" "$cores"
    fi
  fi
  printf '%s\n' "$workers"
}

# The caller's own scheduling priority. Split out so it can be stubbed in tests the
# way nadili_gate_cpu_count is: the gate runs its own pytest leg under this very
# prefix, so a test that assumed the runner sits at nice 0 passed standalone and
# failed inside the gate -- which is exactly what happened before this was extracted.
nadili_gate_current_nice() {
  local current
  current="$(ps -o nice= -p $$ 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ ! "$current" =~ ^-?[0-9]+$ ]]; then
    current=0
  fi
  printf '%s\n' "$current"
}

# Scheduling priority for the CPU-heavy legs. This is deliberately `nice` and not
# `taskpolicy -c background`: the macOS background tier confines a task to efficiency
# cores and throttles its I/O, which on this host is a large unmeasured wall-clock
# risk. `nice` only lowers scheduling priority, children inherit it, and the owner's
# editor and agent renderers stay at nice 0 -- so they win the CPU whenever they want
# it while a gate on an idle machine is unaffected.
#
# Prints a whitespace-separated prefix for array expansion at the call site, or
# nothing at all when the prefix must not be applied. Fail-open like everything else
# here: an unusable value, missing `nice`, or an OS that denies `setpriority` yields no prefix.
nadili_gate_can_nice() {
  local increment="$1" output
  output="$(nice -n "$increment" true 2>&1)" || return 1
  [[ -z "$output" ]]
}

nadili_gate_nice_prefix() {
  # `-` rather than `:-` on purpose: unset means "take the default", while an
  # explicitly empty value means "no prefix", same as 0. With `:-` the empty case
  # would silently collapse into the default and the disable branch would be dead.
  local level="${NADILI_GATE_NICE-10}"
  if [[ -z "$level" || "$level" == "0" ]]; then
    return 0
  fi
  if [[ ! "$level" =~ ^[0-9]+$ ]]; then
    printf 'gate-slots: invalid NADILI_GATE_NICE; running without a nice prefix\n' >&2
    return 0
  fi
  if ! command -v nice >/dev/null 2>&1; then
    return 0
  fi

  # Self-suppressing under nesting. `nice -n` is RELATIVE to the caller, so a gate
  # that nices a wrapper which nices its own leaf would land at 2x the level --
  # observed here: web-check.sh nices the Admin build, whose pnpm script is
  # admin-web-test.sh, which nices again, for an effective nice 20 while the public
  # build sat at 10. Reading the current level and declining to add a second prefix
  # keeps the outcome at exactly `level` no matter how the scripts are composed, and
  # is robust to call chains added later. Unreadable level: skip the prefix rather
  # than risk compounding.
  local current
  current="$(nadili_gate_current_nice)"
  if [[ ! "$current" =~ ^-?[0-9]+$ ]]; then
    return 0
  fi
  if ((current >= level)); then
    return 0
  fi
  local increment="$((level - current))"
  if ! nadili_gate_can_nice "$increment"; then
    printf 'gate-slots: nice denied; running without a nice prefix\n' >&2
    return 0
  fi
  printf 'nice -n %s\n' "$increment"
}

nadili_gate_release() {
  if [[ "${nadili_gate_shell_lease_owned:-}" == 1 ]]; then
    local lock_root token
    lock_root="$(nadili_gate_lock_root)"
    token="${NADILI_GATE_LEASE_TOKEN:-}"
    if [[ -n "$token" ]]; then
      "$(nadili_gate_python)" "$(nadili_gate_executor_path)" release-fd \
        --lock-root "$lock_root" --fd 9 --token "$token" || return 1
    fi
    exec 9>&-
    unset NADILI_GATE_LEASE_FD NADILI_GATE_LEASE_ROOT NADILI_GATE_LEASE_TOKEN
    unset NADILI_GATE_LEASE_OWNER nadili_gate_shell_lease_owned
  fi
  nadili_gate_slot_dir=""
}
