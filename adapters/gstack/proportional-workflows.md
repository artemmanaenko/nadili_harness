---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Proportional workflows — v1

Owns risk routing and role allocation for new gstack plans. The seven-stage process owns
outcomes, owner approval/start, independent verification and all required gates.
Record `Workflow policy: proportional-v1`, `Workflow route: Small|Standard|High-risk` and
`Route reason:` in the plan. Approved plans without this policy keep the legacy adapter
and its roles; migration needs explicit owner approval and an evidence review. The shared
budget hard-stop correction applies to dispatch regardless of routing policy; it does not
silently rewrite legacy role allocation. Missing or
unrecognized policy/route on a new plan uses the conservative legacy process, not Small.

Hotfix is a separate entry under `shared/skills/nadili-hotfix/SKILL.md`, not an automatic Small
route. Its risk assessment selects relevant verification; this document's plan-review roles and
approval/start sequence apply to planned work. Hotfix retains the shared budget stop contract.

## Selection and revalidation

At planning entry, after blocker checks and one bounded discovery pass over the spec,
affected ownership/contracts and focused tests, select the highest applicable row below.
Do this before expensive reviewer dispatch. Do not scan unrelated modules or spawn discovery
agents to classify an obvious local change. State remaining uncertainty rather than extend
discovery indefinitely. File count and lines changed are never the risk criterion.

| Route | Required facts / risk floor |
|---|---|
| Small | Local, reversible, known pattern; bounded single-consumer behavior; no risk below |
| Standard | Cross-consumer change or new logic, with understood boundaries and failure modes |
| High-risk | Authentication/authorization, secrets/security, durable data/migration, compatibility, concurrency, production operations, architectural ownership change, or unresolved consequential uncertainty |

Unknown impact means High-risk until evidence resolves it. A documentation-only change to
a security or delivery invariant is assessed by its effect, not its extension. Routine
workflow wording and existing local CLI guards do not automatically change product architecture.
The owner may request a stricter route; neither owner preference for speed nor a model's
confidence lowers an applicable risk floor. Present route/reason in normal plan approval:
no third entrypoint, mandatory route menu or extra owner approval question.

Revalidate before implementation, before review against the complete actual diff, and after
scope/contract changes. Record the delta and reason. Raise the route immediately when a
risk floor appears; add only newly required evidence/roles, do not restart unaffected stages.
New product scope or acceptance changes still require owner approval. A downgrade after
approval needs owner approval and evidence that the risk disappeared. Relevant implementation,
test, configuration or canonical-contract edits invalidate affected review/QA evidence.

## Roles and bounded work

| Route | Plan and architecture | Implementation / independent review | QA |
|---|---|---|---|
| Small | Separate Sol/medium CEO/product reviewer when that lens applies; coordinator applies design/DX lenses; explicit architecture no-impact, otherwise raise route | One implementer, one independent code reviewer | Independent focused observable scenarios for each applicable route |
| Standard | One report-only plan reviewer combines applicable product/design/DX lenses; separate architect only for a concrete architecture decision | One implementer, one independent code reviewer | Independent risk-relevant backend/frontend scenarios |
| High-risk | Combined plan review plus only specialists justified by named risks; architect for architecture impact | One implementer by default, independent reviewer plus only necessary specialist evidence | Independent failure/security/data/concurrency scenarios appropriate to the named risk |

For Small CEO/product review, dispatch `adapters/gstack/roles/small_product_reviewer.toml`
with fresh context (`fork_turns=none`). Do not replace it with a coordinator self-review or
the Sol/high `reviewer` role. Reserve its thread and review pass in the same item ledger;
the two-pass allowance is unchanged. The coordinator's model and independent code-review
model remain unchanged. If the native role is unavailable in the current session, use a
fresh default agent explicitly set to `gpt-6-sol` / `medium` with that role's contract.

Stage 2 is never skipped: no-impact is a recorded decision, not an architect dispatch.
Each specialist needs a named unresolved question, scoped inputs and expected output.
No automatic three-lens fan-out. The same independent person/agent may cover multiple review
lenses and later QA, but must never have authored the implementation. QA requires a fresh
context invocation under Stage 5/6 and remains a separate frozen-candidate phase with
independent scenarios and real runtime observations, not a renamed
unit suite or substituted code review. Frontend QA applicability follows user journeys, not
extensions; no UI/contract impact means an explicit not-applicable verdict. Required checks,
no open P0/P1/P2, owner start and exact integration remain unchanged for all routes.

Each handoff contains item/worktree, spec and approved decisions, exact task and owned paths,
candidate/diff identity, acceptance/failure oracle, focused checks and remaining budget. Pass
paths and relevant deltas, not copied full documents or conversation history. Fresh independent
review starts with `fork_turns=none`; executor reuse receives a bounded delta brief. Do not
spawn an extra coordinator or load every skill. Never remove executor tool restrictions.

## Stop and escalation contract

Two formal review-and-correction reservations per approved scope. A failed or interrupted
review call with no verdict remains an accounted attempt and may receive at most two diagnosed
retries on its existing reservation; it does not consume another review round. Record the failed
attempt, its bounded diagnosis and the retry link in the ledger. A completed verdict always
consumes its round, including `REQUEST_CHANGES` and `NEEDS_REWORK`. Repeated-failure recovery and
other model ceilings still apply.
An `APPROVED` candidate repeated only after a rebase is a recorded `review-refresh`, not another
formal round, when the runner proves that the item-path delta is empty. The refresh still reviews
upstream impact; any item correction or review after a refresh finding uses an ordinary reservation.
After the second, stop if blockers remain or the corrected candidate needs another review.
No third model review without explicit owner-approved budget amendment. A closure review is
a review; changing reviewer, scope name, reservation ID, model or calling it QA does not reset
the allowance. Do not split one scope to create additional allowances. New approved scope
must be recorded explicitly. At exhaustion, report remaining findings, current evidence and
the smallest requested extension; never deliver stale or failing evidence.

The ledger's existing review/thread/live-eval limits are hard dispatch ceilings. `recover`
can authorize a targeted retry only within them; it cannot increase them. `amend` requires
the owner's actual explicit approval, not an agent-authored justification. Reservation alerts
are bookkeeping, not launch permission: a new launch requires `start` with `created: true`.
Any currently exceeded reserved model ceiling blocks new model dispatch globally for that
item, including other scopes; reservations do not disappear just because dispatch was refused.
Gate overages still use targeted recovery; no budget decision waives a required gate.
These are action limits, not a token quota: native app calls and coordinator turns cannot
be intercepted by repository code. Record unavailable usage honestly; never claim billing
or total-token enforcement. Reusing an executor still records each attempt and must stay
within the planned batch/correction allowance; no open-ended debugging conversation.

Use the current owner chat as coordinator, not a newly spawned expensive model. Existing
Luna/high executor, Small CEO/product Sol/medium, initial code-review Sol/xhigh and correction-review
Sol/medium defaults are provisional until NAD-329 measures accepted-result cost and quality.
Architecture specialist uses the existing architect default.
Do not promise measured savings. Escalate a model only for a named reasoning/architecture
requirement or the same failure class after a targeted correction; record why the cheaper
route failed and the expected resolving evidence. Escalation spends the same allowance and
cannot create a third review. Never escalate model size to fix an environment/permission error.

## Acceptance fixtures and lifecycle traces

These policy fixtures are reviewed against the table, not an automatic file-path classifier.

| Fixture | Facts | Expected |
|---|---|---|
| R1 | Local label correction; existing component; no shared layout or contract | Small |
| R2 | New planner freshness logic used by API and admin UI | Standard |
| R3 | One-line workspace authorization filter change | High-risk |
| R4 | Database migration, durable replay or lease algorithm | High-risk |
| R5 | Two-file change with unknown compatibility impact | High-risk |
| R6 | Documentation removes an authentication requirement | High-risk |
| R7 | Local bounded operator guard retaining existing ledger locking/schema | Standard |

| Trace | Expected outcome |
|---|---|
| Small gains a shared API consumer before implementation | Raise to Standard; add contract and relevant journey evidence; retain unaffected plan decisions |
| Standard gains a durable migration in the diff | Raise to High-risk; resolve design and scope approval before implementation/review continues |
| Approved legacy plan has no policy marker | Retain legacy roles; do not auto-migrate |
| Owner requests Small for an authorization change | Keep High-risk floor; explain within normal plan decision |
| Second review requires changes | Fix within scope; stop if fresh review is required; no third dispatch via recover |
| Environment failure repeats | Diagnose environment with focused check; no model escalation or budget reset |
| Relevant correction follows passing QA | Invalidate affected review and QA; rerun within allowance or stop |

Rollback: select legacy for new plans; preserve active approved policies until an explicit
owner migration decision. Do not erase ledgers or remove hard stops as a rollback shortcut.
