---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

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
| Release | Full fresh `pre-release.sh` on the immutable release candidate; no item receipt qualifies production. |

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

The fast hook selects staged changes; empty staging reports an actionable error rather than
silently starting a broad suite. Integration accepts a complete range or deliberate staged/dirty
scope; unknown or empty integration scope stays conservative. `dev-check.sh` uses affected dirty
scope and is not release qualification. Pure Admin/Public changes select that web consumer plus
shared clients/UI checks; shared inputs select both. Missing Admin flow mapping requires complete
Admin coverage, not unrelated backend tests. Mixed Python/tooling selections execute each selected
test once. Existing leases and worker budgets govern actual execution, including cache misses.

A red required check blocks delivery. Diagnose with focused checks before retrying an expensive
wrapper. Retain logs and report executed versus reused checks without claiming runtime evidence
from static or unit results. Account for actual gate attempts in the existing budget ledger;
cache reuse does not renew review, model or retry allowances.
