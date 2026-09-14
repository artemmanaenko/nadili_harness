---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# gstack adapter

Active for **new** Nadili items when `AGENTS.md` selects `gstack`. Existing plans keep their
recorded adapter; a plan without `Workflow adapter:` remains TRIP. This file maps gstack's
useful methods onto `shared/process/SKILL.md`. Nadili owns Linear, the repository spec, item worktree,
plan and review files, QA verdicts, gates, fast-forward integration and candidate release.
Read only the gstack skill needed for the current stage; its mandatory actions must fit the
Nadili stage before invocation. Reading a review rubric is allowed when executing the entire
vendor skill would create conflicting artifacts or actions.

| Stage | Method and Nadili output |
|---|---|
| Discussion | Use gstack `office-hours` product/design questions as prompts for the conversation. Compare options; give at least two distinct designs for UI work. Do not run its full document-writing workflow, `/spec`, `/design-shotgun` or another artifact-writing skill before an explicit planning request. |
| 1 Plan | Follow `docs/CHAT-TO-LINEAR-INTAKE.md`: check `blockedBy`, create the Linear issue in `Specifying`, then the `work/<ID>` worktree, spec and `docs/work/<ID>/plan.md`. Record `Workflow adapter: gstack`, UAT/Done, release type, Backend QA and Frontend QA routes. Review scope with the CEO lens for product work; design lens for UI/UX; DX lens for developer-facing work. Use the installed `plan-ceo-review`, `plan-design-review` and `plan-devex-review` review criteria as relevant, but retain the repository spec and write decisions into the Nadili plan. A proposed scope change returns to the owner and the spec before approval. |
| 2 Architecture | Review the draft plan with the `plan-eng-review` architecture, failure, data/migration, security and test lenses. Record the decision or no-impact verdict in the plan; use a focused canonical ADR when needed. Return scope changes to Stage 1. Present the reviewed plan for owner approval, commit and push it with the spec path in the plan, move only the item to `Ready for Development`, then stop until the separate implementation start. `docs/ARCHI.md` stays a map of implemented architecture. |
| 3 Implement | Implement the approved Nadili plan in its item worktree. Gstack's root-cause investigation method can help with a bug; no gstack completion marker substitutes for plan tasks, canonical docs, generated contracts or project checks. Do not adopt a vendor's auto-checkpoint commits in the item worktree. |
| 4 Code review | Sync canonical architecture and `docs/ARCHI.md` from the implemented code before review. Run `shared/scripts/codex_code_review.py` as specified in `adapters/gstack/orchestrator.md`; it applies gstack `review`'s scope-drift, completion and critical-pass criteria as a **read-only rubric** through a fresh minimal `codex exec`. The installed `/review` is fix-first and edits code, so do not invoke it on the candidate. Write `docs/3-code-review/CR_NAD-<ID>.md` using `shared/process/review-checklist.md` and `shared/process/cr-template.md`. Fix P0/P1/P2; record and defer P3. |
| 5 Backend QA | Dispatch an independent fresh-context tester with the repository spec, plan and frozen candidate. Use `shared/process/testing.md` for real HTTP/job/CLI behavior and persisted effects. Gstack has no general backend QA skill. Record observed evidence and the verdict under `Backend QA` in `docs/work/<ID>/verification.md`. |
| 6 Frontend QA | Dispatch an independent fresh-context tester on a disposable local app. They may invoke gstack `/qa-only` **only** when its effective `CHECKPOINT_MODE` is `explicit`, an isolated local URL and test identity are ready, and its preamble/browser need no owner onboarding, sign-in or setup. Give it the exact target URL, scenario and planned viewport scope; Admin CRM-only defaults to desktop under `shared/process/SKILL.md`. Normalize its report into `Frontend QA` in `verification.md`. If any prerequisite fails, use project Playwright/browser tools under `shared/process/testing.md` instead. Never use `/qa`, which fixes and commits. |
| 7 Deliver | Use Nadili `shared/process/SKILL.md` Stage 7 and the project-owned `shared/process/integrate.md` Steps 10–14: ticket changelog, explicit staging and hook, exact affected integration check, fast-forward `HEAD:main`, bounded Linear update and guarded cleanup. No gstack shipping command participates. |

Hotfix entry uses `shared/skills/nadili-hotfix/SKILL.md` and one hotfix contract instead of
Stages 1–2 artifacts and plan-review roles. Use the Stage 4–7 methods above with that contract;
no vendor planning workflow is invoked. Existing review/QA independence and budget limits apply.

New plans use `adapters/gstack/proportional-workflows.md`: record policy/route/reason before costly
reviews. Its role matrix replaces the legacy fan-out below for recorded proportional items.
Stage 2 no-impact needs a reason, not a separate architect. Specialists need a concrete risk.
Stage 4 uses the runner's two explicit modes: the first pass is an exhaustive Sol/xhigh review of
the complete candidate; a correction pass is Sol/medium over the prior findings and bounded fix
patch, with direct dependency tracing. The runner generates the correction packet and retains prior findings; see `adapters/gstack/review-cycle.md`.
An explicitly broader pass needs a reason and remains within the same allowance. Independent runtime QA remains required where
applicable. Model defaults are provisional until NAD-329 validates them.

In Codex follow `adapters/gstack/orchestrator.md`. Keep the owner's coordinator; use bounded Luna/high
executor batches and the Stage 4 review modes above, except Small CEO/product plan review uses
a separate Sol/medium `small_product_reviewer`; Small design/DX stays with the coordinator.
At most two subagents may be open. Reserve
and start each dispatch in the external ledger. Model-budget overages require explicit owner
amendment: alerts or recovery alone cannot authorize continuation. Gate overages retain targeted
recovery, never a validation waiver. TRIP items retain their adapter.

Legacy plans without `Workflow policy: proportional-v1` retain the following role allocation.
Plan review uses a fresh, report-only `reviewer` for each applicable product, design or DX
lens; architecture review uses a separate fresh `architect`. Give each the repository spec,
draft plan, focused code/ADR context and the relevant gstack criteria. They return findings
and a verdict; the coordinator alone
edits the canonical plan. Record each lens, reviewer verdict, resolved P0/P1/P2 findings and
deferred P3 observations in that plan. Run the engineering/architecture review after the other applicable
plan lenses so it assesses their final proposal. Owner choices during planning settle scope;
neither role auto-accepts expansions.

For browser QA, the tester derives scenarios from the spec and candidate diff under the
Nadili protocol before invoking `/qa-only`. During that optional gstack invocation, supply
the fixed local URL and scenarios; `/qa-only` executes browser checks and writes its own
report. The tester then records the Nadili verdict in `verification.md`.

The installed `/spec` files a GitHub/GitLab issue and archives its own spec; Nadili intake
uses Linear and a repository spec, so do not invoke `/spec` for an item. `/autoplan`
auto-decides some scope expansions and writes its own artifacts; the repository spec and owner
planning decision rule out invoking it. Full `plan-*-review` workflows also require their own
question, TODO, local-task and review-report side effects. Use their focused criteria, not
their entire workflow, unless every mandatory action has been checked against the current
Nadili stage. A gstack review log, score, TODO or `GSTACK REVIEW REPORT` is optional input,
never Nadili readiness or review evidence by itself.

Do not dispatch a full `plan-*-review` into a background/spawned reviewer: the installed
gstack preamble auto-selects recommended answers in that mode, including scope choices.
The Nadili reviewer reports findings; the owner decides any product or design change during
planning. An accepted scope change updates the repository spec before plan approval and commit.

The installed `/ship` bumps VERSION and the aggregate changelog, pushes and creates a PR;
`/land-and-deploy` expects that PR. Neither is Nadili ticket integration. Here “ship an item”
means Stage 7 above. Production versioning and deployment remain with
`../production-release.md` and `scripts/release-candidate.sh`.

On a QA defect, the implementer fixes it, Stage 4 refreshes its affected checks/review, and
the independent tester repeats the affected routes on a new frozen candidate. A vendor
workflow cannot waive Nadili's exact gate, P0/P1/P2 rule, independent QA, no-PR integration or
release boundary. If a gstack skill changes mandatory behavior in an upgrade, skip that full
skill until this mapping is rechecked; continue with the project-owned stage protocol.
