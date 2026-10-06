# Codex orchestrator for Nadili items

This is a concise fork of the batch loop in
`adapters/TRIP/implement.md` and the review loop in
`.claude/skills/codex-code-review/`. It replaces their CLI transport for **gstack items**;
the Nadili process still owns stage gates and outcomes. The current owner chat is the
orchestrator with its chosen model and mode. Do not spawn or pin another orchestrator.
`adapters/gstack/roles/executor.toml` is Luna/high/fast with a bounded skill catalog and settings
that restrict apps, web, image and nested-agent tools. It does not by itself prove a local-only
tool surface: inherited MCP configuration and live runtime overrides must also be checked.
Before assigning work that requires local-only execution, verify the executor's effective tools
and permissions. If external tools remain available or the restriction cannot be verified,
stop that assignment and report the unmet prerequisite;
`adapters/gstack/roles/reviewer.toml` is Sol/high/standard for product/design/DX plan review;
`adapters/gstack/roles/small_product_reviewer.toml` is Sol/medium/standard for Small CEO/product review;
`adapters/gstack/roles/architect.toml` is Astra/medium/standard only for architecture.
At most two subagents may be open, excluding this chat.

## Hotfix contracts

For the `nadili-hotfix` entry, use `docs/work/<ID>/hotfix.md` wherever execution, review or QA
expects a spec/plan. Do not dispatch plan reviewers or require Ready for Development. The
coordinator may implement a bounded delta directly. Other delegation retains the existing
role/model settings and ledger; pass the hotfix contract, candidate and exact scope in handoffs.
The review runner accepts `--hotfix docs/work/<ID>/hotfix.md` in place of `--spec` and `--plan`.
All review-entry/commit/integration gates, independent QA and action limits below still apply.

## QA dispatch contract

Stages 5/6 use native fresh-context agents (`fork_turns="none"`), never implicit model
inheritance. Backend QA uses `adapters/gstack/roles/backend_qa.toml`: Sol/medium/default tier.
Frontend QA uses `adapters/gstack/roles/frontend_qa.toml`: Terra/medium/default tier.
Select before dispatch using the actual QA risks: backend concurrency, migrations, recovery
after failure or complex authorization requires Sol/high; frontend complex multi-step journeys
with authorization or ambiguous state transitions requires Sol/medium. Record the concrete
reason in the handoff and ledger; the item's route label alone does not upgrade every QA stage.

For an elevated setting, use a fresh default agent with explicit `model` and
`reasoning_effort`, passing the corresponding QA role's complete developer contract. Do not
try to override a named role's pinned TOML settings. Use the same explicit-default fallback
if a newly added named role is unavailable in the active session. Record the selected model
and effort at ledger start; never substitute the coordinator's model implicitly.

Supply spec/plan paths, frozen candidate, bounded scope, runtime/test identity and resource
limits, evidence output paths and remaining budget; do not fork implementation history.
QA derives independent scenarios and observes runtime evidence; it does not repeat full code
review or add permanent tests. Permit disposable probes and test data, but no product edits,
commits, deployment or nested agents. Keep tool output and the evidence report bounded.
Retest on the same selected model at medium effort, limited to defects and affected scenarios;
if a named hard-reasoning risk remains, retain backend high and record why. A failed check or
environment setup alone never triggers model escalation or a new full QA pass. Every invocation
still consumes the existing budget; these settings grant no extra attempts.

New plans record `Workflow policy: proportional-v1` and follow
`adapters/gstack/proportional-workflows.md` for classification, role allocation,
bounded handoffs and model escalation. Revalidate route against scope and actual diff.
Its matrix supersedes the legacy plan-review fan-out below only for those plans.
Missing policy on an approved plan preserves its existing workflow.

## Cost budget and mandatory preflight

Before opening a native agent thread, dispatching a live provider evaluation, starting a formal
review pass, or running a full fast gate, create and reserve an item-local ledger with
`venv/bin/python shared/scripts/codex_orchestration_budget.py`. The ledger enforces existing model
action ceilings at `start`, not provider token billing. Native tools can bypass repository
code; agents MUST NOT bypass preflight. Reservation records overages with exit-zero alerts,
but that is not dispatch permission. Review/thread/live-eval overages require explicit owner
`amend`; `recover` cannot waive them. Stop with remaining findings and evidence at exhaustion.
Never reset the ledger, rename scope to evade its limit or silently expand targets.
Use opaque action IDs and concise technical
reasons only: never place user queries, source text, credentials or secret paths in the ledger.

```bash
venv/bin/python shared/scripts/codex_orchestration_budget.py init \
  --worktree <absolute-item-worktree> --item-id NAD-<ID>
venv/bin/python shared/scripts/codex_orchestration_budget.py reserve \
  --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --id luna-implementation --kind agent --agent-id luna-executor --fork-turns none
```

The command prints one canonical state path under the repository Git common directory. Both a
worktree and its parent checkout therefore share one 0700/0600 ledger; a committed v1 file is an
import source only. Use `state-path` to inspect it and `report` for bounded evidence. Every actual
dispatch reserves, then immediately `start`s one opaque attempt; every exit `finish`es it with a
terminal status. Missing provider fields are unavailable, never zero; permission-reviewer and
human time remain unknown unless explicitly observed. A running attempt after restart must be
closed with `reconcile` before retry.

`start` returns `created: true` only for a new attempt. A replay is bookkeeping, never
authorization to launch a second process; dispatch requires the explicit true result.

For each native agent or gate, use a deterministic reservation/attempt pair and record all terminal
fields. For an unavailable provider usage channel, omit token flags (the ledger stores unavailable):

```bash
venv/bin/python shared/scripts/codex_orchestration_budget.py start --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --reservation-id luna-implementation --attempt-id luna-implementation-attempt-1 \
  --stage implementation --model gpt-6-luna --effort high --category model
venv/bin/python shared/scripts/codex_orchestration_budget.py finish --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --attempt-id luna-implementation-attempt-1 --status succeeded --result-code completed \
  --elapsed-ms 12000 --usage-source unavailable
# Only use reconcile for a stale running attempt after a restart or crash.
venv/bin/python shared/scripts/codex_orchestration_budget.py start --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --reservation-id luna-implementation --attempt-id luna-implementation-attempt-2 \
  --stage implementation --model gpt-6-luna --effort high --category model
venv/bin/python shared/scripts/codex_orchestration_budget.py reconcile --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --attempt-id luna-implementation-attempt-2 --result-code recovered-after-restart
```

Defaults are eight distinct agent threads, 12 live provider calls, two formal review rounds per
scope, six full fast gates, and two final integration checks. These are ceilings, not required run
counts. Existing item ledgers retain their recorded limits. Reusing a thread does not consume a
new thread, but still needs a self-contained brief and must not turn into open-ended exploration.
`fork_turns="all"`, unbounded history, a non-fresh reviewer and missing static evidence produce
ledger alerts; default to `fork_turns="none"`, and add a written exception for bounded history of
1–4 turns. A requested 24-call eval run should have an explicit recorded owner approval before
dispatch:

A runner-proven rebase-only refresh after an `APPROVED` verdict uses `review-refresh`. It records
the model attempt and usage but does not consume a formal review round. The runner grants this only
when the base changed and the item-path delta is empty; the generic Ledger reserve command rejects
manual selection. The refresh still checks upstream impact, and review after a finding is ordinary.

```bash
venv/bin/python shared/scripts/codex_orchestration_budget.py amend \
  --worktree <absolute-item-worktree> --item-id NAD-<ID> \
  --owner-approval "Owner approved 24 calls after static and local checks." \
  --max-live-eval-calls 24
```

Run the live-eval ladder in order: static contract/test, up to four deterministic local cases,
then the planned 12 or explicitly approved 24 live calls. After corrections, use targeted
micro-checks. The final fast gate belongs to the verify-enabled commit hook: do not run
`pre-commit-fast.sh` manually immediately before `git commit`. Reserve each actual hook attempt;
after a failure, fix with focused checks and retry the commit. The final integration check is
distinct and remains mandatory. Record the terminal `status` output in the item verification
evidence.

Check ownership and reuse are defined once in `shared/process/gates.md`.
Stage 4 records affected checks; Stage 7 commits with the hook and validates the final candidate
through the affected integration entrypoint. Do not add broad runs for stage transitions.
Record executed and reused checks separately; every actual wrapper attempt retains its budget
accounting. Explicit full local/release qualification is fresh and unreduced.

Two matching terminal failure signatures or a new v2 overage creates a blocking trigger. Before
an expensive retry, run a bounded targeted check and record `recover --trigger-id ...
--next-attempt-id ... --targeted-check-result passed --next-action ...`. The authorization is
one-shot and cannot reset limits or waive required gates. Model overages cannot be recovered
until the owner explicitly raises the applicable limit; gate overages remain recoverable.
Reports distinguish reserved-without-
attempt, terminal and running attempts and use interval unions for overlapping activity. Legacy
v1 alerts are `legacy_unresolved` observations and never authorize recovery.

Use trigger IDs returned by `report`; IDs are opaque. If several blocking triggers apply,
record each through `recover` for the same next attempt and identical check/action metadata.
Dispatch requires coverage of all applicable triggers and consumes them atomically.
Before `rollback-export`, finish or reconcile every running attempt. Export freezes all
state-changing lifecycle/budget commands until the bound `rollback-reconcile` succeeds.

At the start of each Nadili stage and when switching skills, post one brief status line:
stage, selected adapter, exact skill, and its purpose. Say “using the criteria of” when a
vendor skill is only a rubric; say “running” only when invoking its full workflow. For
example: “Discussion: using gstack `office-hours` questions”; “Planning: using the criteria of
gstack `plan-ceo-review`”; “Backend QA: running `nadili-backend-qa`”. Mention a change
once; routine subagent resumes and internal tool calls need no new announcement.

### Current task title transitions

For gstack work, call Codex's current-task title operation when active planning begins, before
presenting a plan or another owner-only stop, on entry to an external or technical Blocked state,
when owner action resolves and work resumes, after integration when the item routes to
owner-verifiable UAT, and at Done or Canceled. An owner-only stop stays `APR` even if Linear uses
Blocked. Apply the prefix and stable-title rules in root `AGENTS.md`; this section defines only
when to perform the operation. If it fails, report the failure once and continue the stage.

## Aside browser transport

When gstack `browse` reports `ASIDE_NOT_RUNNING` but the owner says Aside is open, treat a blocked
loopback connection as a likely cause before choosing the headless fallback. Report the mismatch
and check the effective runtime permissions under the Runtime permissions contract in
`shared/process/SKILL.md`. If interactive approval is available, submit only the
failing `aside repl` or `aside exec` command for one-shot approval through that mechanism;
do not create a broader reusable rule or add a separate owner confirmation. If approved, retry
the exact command once. When approval is unavailable or the retry fails, use an authorized
headless fallback. After an explicit denial, use that fallback only if it is materially safer
and respects the denial's rationale; otherwise report the transport blocker under the shared
contract. Handle review timeouts under that contract before retrying. The escalation changes
transport only: the browse skill's target, tab-isolation, credential, and mutation-consent rules
remain binding.

## Plan and implementation batches

Stage 1 plan checkboxes must give Luna clear behavior, failure cases, dependencies and
focused checks. At the owner's separate Stage 3 start, pass the Nadili readiness check,
then batch those checkboxes. A batch is the smallest set that leaves the tree green;
never split an interface from its wiring or cross a plan phase. Prefer a thin vertical
slice and a reviewable diff (roughly 300 changed lines; a larger checkbox stands alone).
Shrink novel, architectural or security-sensitive batches; a low-risk phase with up to
3–4 checkboxes may be one batch. Keep owner-only, credential, dashboard and ops work out
of executor assignments.

Use one persistent `executor` thread across sequential batches by default. For each batch,
give the item ID, **absolute item worktree path**, committed spec path from the plan,
full plan path, exact checkboxes to implement now, owned write set if parallel, and focused lint/build
commands. State that future checkboxes are out of scope. Carry 1–3 sentences of correction
notes into the next batch. Two executors are allowed only for independent write sets and
runtime resources; neither edits the plan checkboxes then. Do not start a reviewer while
both executor slots are occupied. No executor commits, changes Linear or integrates.

After each `IMPLEMENTATION_COMPLETE` or `IMPLEMENTATION_PARTIAL` handoff, inspect only the
batch delta with `git -C <absolute-worktree> status -s` and `git -C <absolute-worktree> diff`
(path-filter when two disjoint writers ran). Compare actual behavior and checked boxes
with the assigned scope. Fix small issues directly, run affected lint/typecheck/build,
and stage **explicit reviewed paths** with `git -C <absolute-worktree> add <paths>` so the
next batch starts with a clean unstaged delta. Never use `git add -A`; do not commit.
Pass what you corrected and why to the same executor on resume. Grow clean batches,
shrink ones needing heavy correction; reset a confused thread only at a batch boundary.
For a substantial incomplete batch, resume with the exact remainder. After the last batch,
read `git -C <absolute-worktree> diff HEAD` once for cross-batch drift, then proceed to
Stage 4 affected checks, architecture sync and formal review under the shared gate contract.

## Independent review loop

Proportional items use the canonical route matrix: separate `small_product_reviewer`
(Sol/medium) for applicable Small CEO/product review, coordinator design/DX lenses for Small, one
combined independent plan reviewer for Standard, risk-justified specialists for High-risk.
Record architecture no-impact instead of dispatching an architect without a decision.
For legacy items dispatch a fresh-context (`fork_turns="none"`) native `reviewer` for each applicable Stage 1
product/design/DX lens and a fresh `architect` for Stage 2. Keep one thread per plan-review lens
through its corrections, then release its slot before the next lens. Give it the item ID,
**absolute item worktree path**, draft spec path and content digest, full draft plan and exact lens.

Stage 4 code review uses the non-interactive runner, never the native `reviewer`:

```bash
venv/bin/python shared/scripts/codex_code_review.py \
  --worktree <absolute-item-worktree> \
  --item-id NAD-<ID> \
  --spec <spec-path-inside-worktree> \
  --plan docs/work/NAD-<ID>/plan.md \
  --candidate <candidate-fingerprint> \
  --diff-base <review-base-commit> \
  --gate-summary-file <bounded-gate-summary> \
  --reservation-id stage4-code-review-round-<N> \
  --attempt-id stage4-code-review-round-<N>-attempt
```

The runner resolves and initializes the canonical Git-common-dir ledger from `--worktree` and
`--item-id`, importing a committed v1 source once when present. Do not pass a tracked
`docs/work/<ID>/orchestration-budget.json` to the runner. `--budget-state <absolute-path>` remains
available only for isolated external test ledgers; relative paths and resolved repository paths
are rejected. The runner reserves and
records one attempt per actual process launch with ignored user config, read-only sandbox, no
persistence, apps, MCP, web or useful skill catalog, and validates structured output. The first
review is a fresh Sol/xhigh exhaustive pass over the complete candidate. It must trace changed
behavior through direct consumers, persistence, concurrency, failure paths and public projections,
and return evidence for every canonical checklist section instead of stopping at the first finding.

The runner owns correction packets and completed-review history; follow
`adapters/gstack/review-cycle.md`. Keep the review base pinned through corrections.
Stage explicit candidate paths and reuse the ordinary command above with new attempt/reservation
IDs. The runner automatically loads the prior verdict, records the index tree and supplies
item corrections separately from upstream changes. Do not prepare a manual patch or start its
ledger attempt. Use `--exhaustive-reason <technical-code>` for an explicitly broader pass; prior
findings remain mandatory and the same allowance applies. Legacy evidence import is described
in the cycle contract.

The coordinator supplies a deterministic new reservation and attempt ID per actual round; for an
authorized retry, pass `--retry-of <prior-attempt-id>` after recording the targeted recovery. The
exception for a review call that ends without a verdict is at most two diagnosed retries on the
same review reservation. Each retry needs a new attempt ID, `--retry-of` naming the immediately
preceding failed or interrupted attempt, and `--retry-diagnosis <bounded-technical-code>` naming
the checked failure and remedy. The ledger retains each attempt, elapsed time, and available usage;
the retries do not consume another formal review round. A completed verdict, even
`REQUEST_CHANGES`, requires a new review reservation for another pass. Repeated-failure triggers
still need targeted `recover`, and no retry may exceed another model ceiling. The
gate summary, prior review and correction patch are bounded transport inputs, not canonical
artifacts. Do not substitute the executor's report for the candidate diff. Stage 3 and later inputs
name the spec path from the approved plan and the candidate fingerprint; no Linear SHA pin is
required.

Require findings grouped P0–P3 with `file:line` or `plan:line`, concrete impact and a
fixable action; only deferred P3 stays one line. Require the reviewed candidate identity,
checklist section outcomes, and exactly one verdict: `APPROVED`, `REQUEST_CHANGES`, or
`NEEDS_REWORK`. `APPROVED` means no open P0/P1/P2; Stage 4 also needs green required checks.

For each P0/P1/P2, read the cited code or plan and either fix it or push back with concrete
evidence. Do not fix P3. After a code fix, run affected checks under `gates.md`; expand coverage
for changed shared contracts, dependencies, gate/runtime configuration or uncertain impact.
Rebase alone is not a reason for a separate broad review-entry run. For code corrections, use the
cycle contract's scenario handoff and generated packet. The structured
result preserves IDs, classifies each finding's origin and accounts for every prior blocker. Stop at the approved review allowance (default two
passes); no third dispatch without explicit owner amendment, even if a fix needs verification.
Different model, reviewer,
scope label or disguised closure/QA review does not renew the allowance. Repeated incorrect
findings need evidence, not silent dismissal. `NEEDS_REWORK` means reassess within the remaining
allowance; scope changes and exhausted budgets require the owner.

After Stage 4 code review converges, the orchestrator writes
`docs/3-code-review/CR_NAD-<ID>.md` from
`shared/process/cr-template.md`: include every finding from all rounds
with its final disposition, all checklist section outcomes and the final verdict.
Plan and architecture review verdicts instead go into the item plan. No specialist edits
these canonical artifacts.
Later semantic changes to reviewed code, tests, runtime configuration or canonical contracts
require affected review/checks. Apply the cycle contract for upstream changes and evidence transfer;
base movement alone does not invalidate review. Correct derived evidence mechanically and validate
its references; a changed implementation claim still requires the affected review.
The batch delta check is not formal review. Backend and Frontend QA still use separate
fresh-context testers on a frozen candidate; give each a self-contained brief rather than
inherited implementation narrative.
