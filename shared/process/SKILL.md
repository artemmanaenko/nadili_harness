---
name: nadili-process
description: Nadili's planned and hotfix delivery process. Owns Linear, worktrees, architecture decisions, implementation, code review, backend and frontend QA, integration and release boundaries. Select a skill adapter from AGENTS.md; vendor skills supply methods, not project policy.
---

# Nadili process

This is the **main delivery procedure** for one Nadili item. Planned work has two
explicit commands: **plan** and **implement**. Before either, discuss the idea and options
without creating a ticket, worktree or repository artifact; a design task gets at least two
distinct design alternatives. `plan` runs Stages 1–2 and stops at `Ready for Development`.
`implement` starts Stage 3 and continues autonomously through review, applicable Backend QA
and Frontend QA, and ticket integration (Stages 4–7). Owner UAT, if needed, follows integration.

The Nadili skills in `.agents/skills/nadili-{discuss,plan,architecture,implement,hotfix,review,backend-qa,frontend-qa,deliver}/`
are stable entrypoints. Each reads its stage below and the selected adapter; it may invoke only
compatible vendor skills. A wrapper cannot suppress a vendor skill's mandatory side effects.
Claude Code uses same-named thin entrypoints in `.claude/skills/` that point to these contracts.
`AGENTS.md` owns product, architecture, security,
standing authorization and the active adapter. Repository configuration and automated gates
own executable checks. An adapter in `adapters/` may choose skills and methods inside a stage;
it cannot change stage outcomes, item artifacts, status semantics or the candidate release
boundary. If a vendor skill has a mandatory conflicting action, do not invoke that skill for
the stage.

`AGENTS.md` selects the adapter for **new** items. Record `Workflow adapter: trip|gstack` in
`docs/work/<ID>/plan.md`. Existing items retain their recorded adapter; old plans without the
line remain TRIP items. Switching an in-flight item requires an explicit owner decision and a
review of completed evidence. Read only the selected adapter and focused project documents,
not every vendor procedure.

New gstack plans use `adapters/gstack/proportional-workflows.md` for risk routing and role allocation.
Record policy, route and reason after bounded discovery, before specialist dispatch.
Include route in ordinary plan approval; no extra owner-choice stage. Approved in-flight
plans retain their process unless the owner approves migration. The policy never waives
stage outcomes, independent QA, safety rules or required gates below.

At each stage transition, tell the owner in one short progress sentence which skill from the
selected adapter is being used and for what. Announce a change of skill or method when it
happens, not every internal call. Name the exact skill; distinguish **running** its full
workflow from **using its criteria**. If the stage uses only a Nadili skill, name that
instead. Do not imply that a vendor workflow ran when only its rubric was read.

## Hotfix entry

`shared/skills/nadili-hotfix/SKILL.md` owns entry for an existing patch or a concrete bounded
fix the owner asks to complete/integrate. Its `docs/work/<ID>/hotfix.md` replaces the spec and
plan; Stages 1–2 and the Ready for Development/second-start ceremony do not apply. This is a
separate entry, not a Small risk classification. Risk still controls verification depth.

For this route, references in Stages 3–7 and their review/QA/delivery entrypoints to the spec or
approved plan mean the hotfix contract, including acceptance, QA and UAT/Done decisions.
Stage 3 readiness uses that contract; proportional planning fields and plan-review roles are
not required. Reassess actual risk before review and preserve the corresponding verification.
Readiness and the formal review runner validate the contract explicitly. Planned-item behavior
is unchanged. The hotfix skill owns authorization, existing-patch transfer, scope and migration;
Stages 3–7 retain gates, independent review/QA, budget stops and the production-release boundary.

## Stage 1 — Plan

1. Begin only on an explicit planning instruction. For a named item, **first** read Linear
   `blockedBy`. A blocker resolves at `UAT`, `Done`,
   `Canceled` or `Duplicate`; an open blocker stops work before discovery, worktree creation
   or status writes. Report every open blocker and check one level deeper before suggesting
   another item. Never switch items without the owner's choice.
2. Use the existing issue and its repository spec when present. Resolve the spec from the
   plan's `**Spec:**` path or the unique `NAD-<ID>-` specification filename in `docs/`;
   a legacy `Spec: <path> @ <sha>` reference remains valid. Read the committed spec from
   the item's branch (or `main` for intake-only work). Its frozen scope needs no rediscovery;
   if it is wrong or incomplete,
   stop rather than editing it silently. For new work, follow
   `docs/CHAT-TO-LINEAR-INTAKE.md`: create the issue in `Specifying` **before** its id-derived
   worktree and plan paths, then freeze the spec in that worktree. Linear is a status surface,
   not product authority.
3. Create or resume `work/<ID>` with `bash shared/scripts/worktree-new.sh <ID>`; draft
   `docs/work/<ID>/plan.md` **inside that worktree**, never in the shared primary checkout.
   Record the adapter, `Verification: UAT|Done` with a reason, `Release type:
   patch|minor|major`, test and documentation impact, and **separate Backend QA and Frontend QA
   routes**. For each route record applicable/not applicable with a reason, the observable
   behavior, runnable boundary, acceptance oracle, environment, viewport scope and evidence.
   Admin CRM-only work defaults to its primary desktop viewport; do not add narrow/mobile or
   general responsive inspection unless the item changes responsive CSS, shared layout/UI, or
   explicitly requires that behavior. A contract or
   backend change may require both routes when it changes a user journey; file extensions do
   not decide applicability. Include `### Interleaving` coverage required
   by `shared/scripts/check_plan_interleaving.py`. A plan declares no version number. Decide `UAT`
   with one question: *does a human looking at the running product change the verdict?*
   UI, UX flow, copy and feel usually do; backend, tests, infrastructure, docs and metadata
   usually do not. Mixed items follow their user-visible part; a genuine tie goes to `UAT`.
   Make implementation checkboxes precise enough to delegate: name the observable result,
   failure behavior, dependencies and focused checks. Form execution batches from these
   checkboxes at Stage 3; the plan does not have to predict every batch boundary.
4. Review the **draft plan** using the selected adapter and recorded route against the repository spec, canonical
   product docs, `AGENTS.md`, failure cases, concurrency and gates. Resolve blocking findings,
   then run the architecture stage before the owner's final plan decision.

Discussion and an approved spec alone do not make a new issue `Ready for Development`.
Existing ready issues are never moved backwards; reconcile their plan under the new contract.

## Stage 2 — Architecture

1. After the draft plan review and **before coding**, review its technical design against the
   spec, canonical ADRs, runtime ownership and `AGENTS.md` invariants. Resolve module and data
   boundaries, public contracts, migration compatibility, failure handling, operational effects
   and test seams. Use the selected adapter's engineering/architecture review method.
2. Record the chosen design and rejected alternatives in the item plan or a focused canonical
   ADR when the decision changes repository architecture. If the review changes product scope or
   acceptance criteria, return to Stage 1 for a revised plan and owner decision. Do not let
   implementation start on an unresolved architectural finding. For an item with no architecture
   impact, record that conclusion in the plan and proceed without a new ADR.
3. Present the reviewed plan and architecture to the owner for approval. On approval, commit
   and push the plan/spec/ADR on `work/<ID>` with the installed hook. Verify the plan names
   the committed spec path and that both files are present on the pushed branch, then move
   this issue to `Ready for Development`. Do not edit Linear to add a SHA. Stop there until
   the owner explicitly starts implementation;
   approval of the plan or the word “plan” alone is not that start. Never use `--no-verify`.
   The plan remains durable on its branch for later implementation.
4. `docs/ARCHI.md` describes **implemented** architecture. Do not publish a planned design as
   current fact; Stage 4 reconciles that guide with the completed implementation.

## Stage 3 — Implement

1. Revalidate the recorded route against implementation scope under `adapters/gstack/proportional-workflows.md`.
   Enter the same item worktree and verify readiness with
   `venv/bin/python shared/scripts/check_item_ready.py <ID>` before moving Linear to `In Progress`.
   Use the selected adapter's implementation method against the approved plan. Keep code,
   generated contracts and canonical documents aligned with the actual change, not only the
   planned file list. Read `docs/coding-standards.md` before Python, tests, migrations or scripts.
2. Stay inside the item worktree. Every Git command uses its **absolute** path. Stage explicit
   item files; never sweep a shared checkout with `git add -A`. `origin/main` moving is normal:
   pin a base for review/corrections; fetch and rebase at final integration. Shared `venv/`, ports, Compose and local env are
   machine-wide; sequence conflicting dependency sets, use disposable stacks and never purge
   another item's state.
3. Finish only when every plan task is done or explicitly recorded out of scope, relevant
   code and docs exist, and no unresolved owner-only decision is hidden. A vendor's completion
   marker alone is not evidence. Handoff to code review; do not integrate yet.

## Stage 4 — Code review

1. **Maintain the architecture guide before reviewing the code.** Read `docs/ARCHI-rules.md`,
   compare `docs/ARCHI.md` with the actual diff and the Stage 2 decision, update it if the
   implemented architecture or its guidance changed, and check its size with
   `LC_ALL=C bash scripts/count-archi-tokens.sh docs/ARCHI.md`. Record “unchanged” with a reason
   when no update applies. Sync other affected canonical docs in the same item.
2. Run focused automated tests, static and contract checks, relevant runtime smoke, and
   `git diff HEAD --check` after staging explicit candidate paths; record exact commands and pass/fail/skip results in
   `docs/work/<ID>/verification.md`. A red required check is a blocker, not a skip.
   Follow `shared/process/gates.md` for check ownership and evidence reuse: review entry needs affected checks,
   not an automatic additional broad fast gate. Commit and release obligations remain mandatory.
3. Review the implementation and architectural documentation against the spec and plan using
   the selected adapter. Move Linear to `Code Review` when review starts.
   `shared/process/review-checklist.md` and `shared/process/cr-template.md` are Nadili's criteria and output contract: write
   `docs/3-code-review/CR_NAD-<ID>.md`; fix P0/P1/P2, record and defer P3. Vendor auto-fix or
   severity instructions cannot expand that scope. A review is stale after relevant code,
   tests, runtime config or canonical contracts change semantically; refresh affected evidence.
   For gstack, `adapters/gstack/review-cycle.md` owns frozen-base corrections and upstream evidence transfer.
   Mere `origin/main` movement and mechanically corrected derived evidence are not new defects.
   A runner-proven refresh after an `APPROVED` verdict and rebase records full attempt evidence but
   does not consume another formal review round when the item-path delta is empty. It still checks
   upstream impact; any item correction or review after a refresh finding consumes an ordinary round.
   No open P0/P1/P2 or unresolved review verdict may enter QA or delivery. Stop at the approved
   review allowance (default two passes), rather than launching another pass without explicit
   owner amendment. A correction needing fresh review is not deliverable at budget exhaustion.

## Stage 5 — Backend QA

1. After code review converges, move Linear to `Testing`. If the plan says Backend QA is not
   applicable, confirm that against the actual diff and record the reason in
   `docs/work/<ID>/verification.md`. Otherwise freeze the candidate and dispatch a
   **separate tester agent with fresh context**. Give them the repository spec, plan,
   candidate revision/diff and isolated runtime setup, not the implementer's success summary.
   The tester does not edit implementation, commit, change Linear or deliver. Keep the
   candidate worktree unchanged while they test. `shared/process/testing.md` supplies the protocol.
2. The tester derives backend scenarios from the spec and diff, then exercises a disposable
   service or job runtime through HTTP, worker, CLI or another real operator boundary. Observe
   responses **and persisted effects**. Cover the main path, failures, validation, tenancy and
   authorization, replay/idempotency and concurrency where relevant. A passing unit suite
   alone does not prove this stage. If an applicable behavior cannot be exercised in an
   isolated runtime, record the exact blocker and stop before delivery.
3. Record expected and observed results, candidate fingerprint, environment and evidence under
   `Backend QA` in `verification.md`. The implementer fixes defects in Stage 3; the independent
   tester repeats affected scenarios on a new frozen candidate. Any implementation fix returns
   through Stage 4 review before QA retest. Do not carry a failing verdict into the next stage.

## Stage 6 — Frontend QA

1. If the plan says Frontend QA is not applicable, confirm that against the actual diff and
   record the reason. Otherwise dispatch a **separate fresh-context tester invocation** for
   the frozen candidate; the Backend QA tester may serve this role if still independent of the
   implementer. Give them the repository spec, plan, revision and local app setup. The same
   read-only, no-commit and no-Linear boundaries apply.
2. Exercise actual browser journeys in a disposable local app against the appropriate backend
   or controlled fixtures. Check the intended outcome and relevant loading, empty, error,
   access-denied and navigation states. For Admin CRM-only work, use the planned primary desktop
   viewport and omit narrow/mobile and general responsive inspection unless the candidate changes
   responsive CSS, shared layout/UI, or the spec/plan explicitly requires it. Public/client UI and
   responsive-impacting work retain relevant responsive coverage. Inspect what the user sees and
   what the browser sends; a component/unit suite alone does not prove this stage. A backend contract
   change that affects a user journey belongs here even if no TSX file changed.
3. Record expected/observed behavior and evidence under `Frontend QA` in `verification.md`.
   The implementer fixes defects, Stage 4 refreshes review and the tester retests a new frozen
   candidate. If any fix can affect the other QA route, rerun that route too. An applicable
   browser path that cannot be
   exercised blocks delivery. Owner UAT remains a post-integration judgment, never a substitute
   for agent QA.

## Stage 7 — Deliver

1. Write `docs/2-changelog/NAD-<ID>.md` with the required `**Object**:` line. Confirm
   `docs/ARCHI.md` and other affected docs were reviewed in Stage 4; if delivery changes
   them, return to the affected review and QA steps. Ticket integration does **not**
   stamp `pyproject.toml`, README's release version or the aggregate changelog.
2. Stage explicit item paths and commit with the hook. Fetch and rebase onto current
   `origin/main`. Run `bash shared/scripts/integration-check.sh --base <merge-base> --head HEAD` on
   the exact commit being delivered. Revalidate a changed candidate through that entrypoint;
   `shared/process/gates.md` permits reuse only when the runner verifies identical eligible inputs. Assess upstream
   conflicts/dependencies under `adapters/gstack/review-cycle.md` for gstack;
   refresh affected review/QA for semantic changes, and record justified evidence transfer otherwise. A red gate blocks delivery.
3. Confirm both QA route verdicts and the review still cover the exact candidate. Push
   `HEAD:main` only as a fast-forward,
   with no PR, merge commit, force-push, ticket version or tag. On rejection, fetch, rebase,
   refresh evidence and retry. After `origin/main` contains the commit, move only this issue
   to the plan's `UAT` or `Done`, update the current Codex task title immediately (`UAT - <title>`
   for UAT; stable human-readable title with no prefix for Done or Canceled), post one bounded
   verification brief, and run the guarded
   `shared/process/scripts/release-cleanup.sh` for its worktree. Report the
   delivered commit, validation, Linear state and cleanup result.

Routine commit, push and cleanup are standing-authorized by `AGENTS.md`; do not ask again.
Honor only a real owner-only blocker or an explicit plan-level confirmation. Failed Linear
writes are reported once, never used as a delivery gate; never copy logs, diffs, secrets or
raw review bodies into Linear. Never weaken a gate or use `--no-verify` to make delivery pass.

## Candidate release boundary

An item reaching `main` is **integration**, not a production release. Only
`production-release.md` and `scripts/release-candidate.sh` reserve a version, stamp aggregate
artifacts, run the full qualification suite, publish a tag and provide signed evidence.
Gstack `ship` and `land-and-deploy` are not Nadili item-delivery steps.
