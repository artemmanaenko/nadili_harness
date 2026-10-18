# Delivery check ownership

`shared/process/SKILL.md` owns stage outcomes. This file owns when checks are required; `shared/scripts/gate_lanes.py`
and the gate entrypoints own executable selection, ordering and evidence. Apply to planned and
hotfix work under either adapter. Do not run a wrapper and its children on unchanged inputs.

## Lifecycle

| Boundary | Required evidence |
|---|---|
| Development and review entry | Focused regression, affected static/contract checks and relevant runtime smoke; record commands and outcomes. Expand coverage for shared or uncertain impact, not merely for a stage transition. |
| Review corrections | Checks for the corrected behavior and affected consumers; refresh review/QA for semantic changes under `adapters/gstack/review-cycle.md`. |
| Commit | Explicit staging and the installed verify-enabled hook. Do not precede it with a redundant manual fast gate. |
| Integration | Run `integration-check.sh --base <merge-base> --head HEAD` on the final clean candidate, after the final fetch/rebase. The runner validates required checks and reuses only eligible evidence. |
| Full local validation | Explicit `pre-release.sh` stays unreduced. The previous production process is removed; this suite and item receipts do not authorize deployment. Never prepare releases during hooks/integration. |

Pin the review base during review and QA. Rebase at final integration; there is no mandatory
pre-review rebase or broad gate solely because main advanced. Conflict-free rebase is not proof
of semantic independence: inspect affected upstream dependencies and refresh relevant review/QA.
A new commit or amend requires integration validation of the new candidate, but identical eligible
check inputs do not require executing the same check again.

## Evidence reuse

`shared/scripts/gate_evidence.py` owns a closed allowlist of local Python static checks and isolated
non-integration, non-harness Python unit tests. It runs the fixed check itself; there is no API
for callers to assert success. Gate obligations remain mandatory even on a reuse hit.

Evidence binds the worktree location, complete source contents/modes, installed Python dependency
and tool contents, configuration and check environment. Commit metadata alone is not an input.
This first version is deliberately conservative: even an ordinary tracked document change can
invalidate evidence. Missing, malformed, altered, incompatible or unknowable evidence means a
cold run. Changed inputs during execution must not produce reusable success. Partial staging
cannot certify a different working tree; stage the intended candidate before commit/integration.

Receipts are private, atomic and authenticated locally. This guards accidental corruption and
unsupported success files, not hostile code with the same OS-user privileges. Never copy a receipt
into the repository or treat it as a production signature. `--fresh` runs an eligible check without
reuse. Explicit `pre-commit.sh` and `pre-release.sh` always execute their complete existing suites.
Import boundaries always run freshly, including after a static reuse hit: they inspect ignored
TypeScript and retired-path existence beyond the cached Ruff/mypy inputs. Web checks, shell
harness, database/runtime tests, formal review and independent QA remain fresh.

## Scope and failures

Local lease `resource_busy` is unexecuted pending work, not a red test result. Follow
[autonomous resource-wait continuation](resource-wait.md) before yielding: preserve the pending
step and arrange a ten-minute same-chat heartbeat in Codex. Admission, required checks,
budget preflight and logs remain mandatory on each actual retry.

The fast hook selects staged changes; empty staging reports an actionable error rather than
silently starting a broad suite. Integration accepts a complete range or deliberate staged/dirty
scope; unknown or empty integration scope stays conservative. `dev-check.sh` uses affected dirty
scope and is not release qualification. Pure Admin/Public changes select that web consumer plus
shared clients/UI checks; shared inputs select both. Missing Admin flow mapping requires complete
Admin coverage, not unrelated backend tests. Mixed Python/tooling selections execute each selected
test once. Existing leases and worker budgets govern actual execution, including cache misses.

Light mode (the commit hook and `--base/--head` integration) selects control-plane paths, test
helpers, container-build files and test files by derived exercise (NAD-459). The derivation runs
once per gate run, and every leg consumes that one routing result. Its reference grammar and failure
contract live in the `scripts/gate_test_selection.py` docstring. Test code counts what it names,
imports and runs. A script counts every repository script its code names and what it imports; only a
name in an executing position must resolve. A dynamic reference outside the grammar fails routing
unless it is registered with its reach. A missing import fails unless the scope deleted it. An
unavailable derivation keeps the previous selection for its paths and is never an empty selection.
Selected cases run once each: the planner subtracts full-lane cases by tree, then by marker through
`pytest --collect-only` (markers are never read statically). A partition that collects nothing runs
nothing, and starts no PostgreSQL container. Selected runs never produce or consume evidence
receipts, so a narrow success cannot certify a broader check. Every `conftest.py`, heavy mode,
`pre-commit.sh` and `pre-release.sh` are unchanged.

Backend application code (`apps/api/` outside its tests) is derived by import reach (NAD-469): a
changed module selects the unit and PostgreSQL cases of every test whose import statements reach
it, through any first-party module, plus every test whose code names its path. A module Alembic
imports also selects the tests that run the migrations. A backend path alone starts neither the
full unit nor the full PostgreSQL lane. These keep the complete selection (static, full unit and
full PostgreSQL) alone or mixed: `apps/api/alembic/**` and `alembic.ini`, `db_schema.py`, `db.py`,
`settings.py`, every package `__init__.py`, and anything a `conftest.py` imports; the dependency
pins under `apps/api/` select every Python lane. A Markdown file follows the docs rule, the
`Dockerfile` the container-build rule, and another non-Python file keeps the complete selection
unless a test names it. Running a module (`python -m`) is not an import, and a run-time import in
reached backend code fails routing unless registered.

The PostgreSQL entrypoint partitions the selected cases into ordinary parallel work and
`postgres_deadline` probes. The latter run serially after the ordinary phase under the same
container and heavy/PostgreSQL lease; each probe retains its original internal concurrency,
dataset and deadlines. The phase selector preserves caller target/marker/name filters and
the two phases together execute the complete selected set exactly once. Both phases run even
after a failure, and any non-empty failure propagates. An empty partition is allowed only
when another partition ran successfully; an entirely empty selection remains exit 5.
Use the marker only for strict wall-clock success probes, not to bypass correctness failures.

A red required check blocks delivery. Diagnose with focused checks before retrying an expensive
wrapper. Retain logs and report executed versus reused checks without claiming runtime evidence
from static or unit results. Account for actual gate attempts in the existing budget ledger;
cache reuse does not renew non-review model or retry allowances. Code-review rounds are telemetry.
