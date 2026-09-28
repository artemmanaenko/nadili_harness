# Implementation procedure (Nadili)

The TRIP implementation of `shared/process/SKILL.md` Stages 3–6. `/TRIP-2-implement` delegates here. This
file is project-owned and is never rewritten by `TRIP-upgrade`.

The main process in `shared/process/SKILL.md` wins. Legacy TRIP details live in
`adapters/TRIP/process.md`: §1 Linear lifecycle (incl. §1.7 dependency gate), §2 worktrees,
§3 P0/P1/P2 policy, §4 plan location and Codex targeting, §5 owner interruption budget.

## Read first

1. `shared/process/SKILL.md` beside this file — the main Nadili process.
2. `adapters/TRIP/process.md` — TRIP-specific details.
3. `docs/ARCHI.md` — current system architecture.
4. `AGENTS.md` — canonical architecture and security invariants (wins over `ARCHI.md`).
5. `docs/coding-standards.md` — **mandatory** before writing any Python, test, migration, or
   script.

## Integration and release boundary

Use `bash shared/scripts/integration-check.sh --base <merge-base> --head <candidate>` for the complete
affected integration union; use `--dirty` only for local development. It reports selected
capabilities and reasons, includes rename/deletion paths, and falls back broadly on uncertainty.
`shared/scripts/dev-check.sh` is the reduced convenience entrypoint. `pre-commit.sh` and
`pre-release.sh` remain explicit and unreduced.

TRIP-2 may finish only after the verification record, affected checks and code review are complete.
`shared/process/gates.md` owns shared check scheduling and reuse for both adapters.
Stage 7 runs the affected gate on the final integration commit. This stage does not publish a
version or tag. Production qualification belongs to one frozen candidate coordinated by
`scripts/release-candidate.sh`; ticket integration does not publish a candidate or run the full
qualification suite.

---

## Step -1: Dependency gate — before anything else

If this session already ran the dependency gate for this item (chained straight from the planning
procedure), skip it. Otherwise check whether the item is blocked (`adapters/TRIP/process.md` §1.7). A blocker
resolves at `UAT`, not `Done`. If blocked: stop, report every open blocker, check one level deeper
before suggesting a substitute, and never switch without the owner saying so.

## Step 0: Enter the item's worktree

Work is isolated before implementing — no need to ask. The default is the item's **worktree**, not
a branch in the shared checkout, because several agents work this repo simultaneously on one Mac.

```bash
bash shared/scripts/worktree-new.sh <ITEM-ID>
cd .codex/worktrees/<item-id-lower>
```

`<ITEM-ID>` is the Linear id (e.g. `NAD-123`). The script is **idempotent** — the worktree usually
already exists from planning, and this resumes it at its current tip. It also:

- creates/resumes branch `work/<ITEM-ID>` from `origin/main`;
- symlinks the primary `venv/` rather than building a second one;
- installs a real per-worktree `node_modules` with `pnpm install --frozen-lockfile`;
- installs the pinned Chromium for Playwright;
- smoke-checks the linked venv, then runs `shared/scripts/worktree-preflight.sh`.

Accept the `work/<ITEM-ID>` branch name — do not rename it.

**Check readiness before touching Linear.** One command, binary answer — do not assess by eye:

```bash
venv/bin/python shared/scripts/check_item_ready.py <ID>
```

It verifies the worktree is registered, that you are standing **inside** it, and that the plan is
present.

- **Exit 0** → move the Linear issue to `In Progress` (`adapters/TRIP/process.md` §1.4) and continue.
- **Exit 1** → fix exactly what it names, then re-run. If you cannot, **stop**: move the issue to
  `Blocked` with a one-line comment naming the blocker. Never set `In Progress` first and revert
  later — an abort goes forward into `Blocked`, never backward with no trace (`adapters/TRIP/process.md` §1.4,
  "No silent reversions").

Never copy a missing plan in by hand — Codex would implement against a path that does not exist,
and `shared/scripts/check_work_item_scope.py` refuses a plan committed off its own branch anyway. A
failed Linear *write* (not a failed precondition) is reported in one line and ignored.

Preconditions the script assumes: a primary `venv/` (`bash setup.sh`), the pinned Node active
(`nvm install`), Corepack enabled. If preflight fails, fix the cause it names — never proceed on a
worktree that failed preflight, because the gates will not be able to run.

**Shared-venv caveat**: `venv/` is symlinked, so a Python dependency change is **machine-global**
— it lands in every worktree at once. If this item changes `requirements*.in`, say so, run
`bash scripts/lock.sh` + `bash setup.sh` deliberately, and expect other agents' runs to pick it up.

### Fallback: a branch in the current checkout

Only when a worktree genuinely doesn't fit — a docs-only change, a hotfix, or you are the sole
session in the primary checkout:

```bash
git fetch origin
git checkout -b feat/[short-description] origin/main   # or fix/[short-description]
```

If already inside this item's worktree or on its branch, continue there — never create a second.

### Cleaning up

The worktree is removed at the end of the release procedure, after the merge. If an item is
abandoned, run this from the primary repo (`shared/process/integrate.md` Step 14's convention — explicit `-C`, not
a bare `cd` + bare `git`):

```bash
git -C <primary-repo-root> worktree remove .codex/worktrees/<item-id-lower>   # --force only after inspecting leftovers
git -C <primary-repo-root> branch -D work/<ITEM-ID>
```

### Concurrency ground rules

Read the plan's **Concurrency & Local Environment Impact** section and honor it throughout:

- **`origin/main` will move. That is normal, never an error.** Do not fight it, do not stop to
  report it. Pin the review base through corrections and rebase at final integration under
  `shared/process/gates.md`; gstack evidence transfer follows `adapters/gstack/review-cycle.md`. Use an absolute worktree path.
- **Regenerate, never hand-merge, generated artifacts.** On a conflict in
  `packages/contracts/openapi.json`, `requirements*.txt`, or a lockfile: take `origin/main`'s
  version, then re-run the generator (`venv/bin/python scripts/export_openapi.py`,
  `bash scripts/lock.sh`, `pnpm install`).
- **Alembic head collisions**: rebase and re-point `down_revision` at the new head. Never merge
  two heads.
- **Ports are contended.** Assume defaults are taken. Prefer disposable stacks
  (`bash scripts/backend.sh check`, `compose.smoke.yaml`), and pass an explicit free port for a
  dev server. If a port is busy, pick another — never kill another agent's process, never ask the
  owner.
- **`~/.config/nadili/local.env` and the local Compose stack are machine-global.** Never run
  `scripts/backend.sh purge`, reset the local database, or rewrite that env file unless the plan
  calls for it and the owner approved it at plan time.
- **Stay inside your worktree.** Never `git checkout` another item's branch, never `cd` into
  `.codex/worktrees/<other-item>`, never touch files outside this item's scope.

---

## Implementation phase — delegate to Codex

You do NOT write the implementation yourself — delegate it via the `codex-implement` skill.
(Exception: trivial unplanned changes of a few lines.)

**Never ask permission to delegate.** Handing Codex the plan and the repository code, and letting
it write inside this item's worktree, is pre-authorized standing for every batch and every resume
(`adapters/TRIP/process.md` "Standing Codex-delegation authorization"). A runtime sandbox/network approval prompt
is yours to satisfy, not the owner's to answer.

Delegation is **batched**: Codex implements a few of the plan's checkboxes per turn, you review
and fix each batch, then request the next with your corrections attached. Same persistent thread
throughout — context and conventions compound.

### 1. Read the plan and decide the batches

- A batch is the **smallest set of checkboxes that leaves the tree green** (compiles, lints).
  Never split an interface from its implementation and wiring.
- Target a reviewable diff — roughly ≤300 changed lines. A checkbox that alone exceeds this
  becomes its own batch.
- Size by risk: novel, architectural or security-critical work → small batches (down to one
  checkbox). Mechanical, repetitive work → larger.
- Never span phase boundaries. The plan is likely sliced vertically, so where possible prefer a
  batch that completes a **thin end-to-end path** over one that builds out a single layer.
- **One-shot escape hatch**: a low-risk plan or phase of ≤3-4 checkboxes is delegated whole.
- **Filter out non-Codex items**: checkboxes needing human input, dashboard/console access,
  credentials, or ops actions are yours — resolve them with the owner, never delegate them.
- **Check the plan's `Verification:` line** (`adapters/TRIP/process.md` §1.4.1). If it is missing — a legacy plan
  or a hotfix — add it to the plan now, before implementing, while the user-visible surface of the
  work is fresh. If the implementation ends up shipping owner-visible behavior the plan did not
  anticipate, upgrade the line to `UAT` and say so in the handoff. Never downgrade a `UAT`
  classification to `Done`.

### 2. Delegate batch by batch

The plan path is `docs/work/<NAD-ID>/plan.md`. The leading sentence is **mandatory** — the vendor
prompt identifies a plan by the hardcoded `docs/1-plans/` path, so without it Codex may implement
without ever reading the plan (`adapters/TRIP/process.md` §4.3):

```bash
bash .claude/skills/codex-implement/scripts/start.sh \
    --prompt-file .claude/skills/codex-implement/prompts/implement.tpl \
    docs/work/<ID>/plan.md \
    "TARGET is the implementation plan — read it in full and implement against it. Implement only: <batch-1 checkboxes>"
```

Each next batch resumes the same thread, carrying your review corrections as `--notes`. Use
`codex-implement`'s **own** wrapper — it pins `STATE_DIR` itself, so there is no export to forget
(a forgotten one silently resumes a *review* thread instead of the implementation thread):

```bash
bash .claude/skills/codex-implement/scripts/resume.sh \
    --prompt-file .claude/skills/codex-implement/prompts/continue.tpl \
    --notes "<what you fixed after the last batch and why; conventions to apply from now on>" \
    docs/work/<ID>/plan.md \
    "TARGET is the implementation plan. Now implement: <next batch checkboxes>"
```

**Parse the trailing tag**:
- `IMPLEMENTATION_COMPLETE` → review the batch.
- `IMPLEMENTATION_PARTIAL` → read the report; resume for the remainder, or finish small leftovers
  yourself during the review.

### 3. Review each batch (delta review)

1. **Review the delta only**: `git -C <worktree-path> status -s && git -C <worktree-path> diff` —
   worktree vs index shows just this batch, since previous batches are staged (step 4). A diff read
   from the wrong tree doesn't error, it silently reviews the wrong code (`adapters/TRIP/process.md` §2.2). Check
   against the plan, `ARCHI.md` patterns, and project conventions (DRY, KISS, comment discipline,
   error handling, naming).
2. **Fix problems yourself** — no back-and-forth with Codex over fixes. What you fixed and why
   becomes the `--notes` of the next resume.
3. **Micro-gate**: run the lint and typecheck commands from the Testing Gate (fast checks only).
   Fix failures now.
4. **Checkpoint**: `git -C <worktree-path> add <paths for this item>` — stage the reviewed batch so
   the next delta review starts clean. No commits; history stays clean for release. Never
   `git add -A` (`adapters/TRIP/process.md` §2.3).
5. Verify the checkboxes Codex ticked match what the diff actually contains.

**Adapt**: clean batch → grow the next; heavy corrections → shrink it and spell out the fix
pattern in the notes. If Codex ignores notes or repeats corrected mistakes late in a long session,
reset the thread at the next batch boundary — the plan file plus a summary note rebuilds context.

### 4. Final pass

After the last batch, read the **full feature diff** once
(`git -C <worktree-path> diff HEAD`). Batch reviews catch local issues; this pass catches
cross-batch drift — duplicated helpers, divergent naming, dead code left by course corrections.
Fix directly.

The testing gate and Codex code review run **once**, after this pass — never per batch.

---

## Testing gate

After implementation, before the Codex review loop. Any failure blocks the loop.

Use the pinned review base. Final rebase and candidate validation belong to integration;
`shared/process/gates.md` defines when earlier evidence transfers and which checks must execute.

### 1. Affected lint, type-check and build

Backend (Python touched): run Ruff format/check and mypy only for affected paths. Read the current
flags from `shared/scripts/pre-commit.sh`; do not invoke that full wrapper here. Integration owns the
final affected union; production qualification owns the full release suite.

Web (TS/TSX touched) — only the affected packages:

```bash
corepack pnpm --filter @nadili/admin-web lint && corepack pnpm --filter @nadili/admin-web typecheck
corepack pnpm --filter @nadili/web       lint && corepack pnpm --filter @nadili/web       typecheck
corepack pnpm --filter @nadili/ui        typecheck
corepack pnpm --filter @nadili/client-ts check && corepack pnpm --filter @nadili/client-ts typecheck
```

`bash shared/scripts/lint.sh` is the auto-fix convenience path for backend work; the explicit commands
above are what the gate reports.

### 2. Affected unit tests

```bash
venv/bin/python -m pytest apps/api/tests/<affected_test_files> -m "not integration" -q
venv/bin/python -m pytest tests/unit -m "not integration" -q   # if shared libs / scripts changed
corepack pnpm --filter <affected-package> test                 # if web changed
```

Only what the change touched — never the full suite by default. `bash shared/scripts/test.sh` runs the
whole backend + unit suite when the blast radius is wide.

### 3. Integration and contract impact

Run each trigger that applies:

- **API schema or operation ID changed** — mandatory, in this same change:
  ```bash
  venv/bin/python scripts/export_openapi.py
  venv/bin/python scripts/export_openapi.py --check
  venv/bin/python scripts/check_openapi_contract.py
  corepack pnpm client-ts:generate && corepack pnpm --filter @nadili/client-ts check
  bash scripts/client.sh  # standalone client contract/type/unit checks
  ```
- **Alembic migration added** — `bash scripts/backend.sh check` (disposable database, never the
  shared daily stack).
- **Admin Web selectors or flows changed** — `bash scripts/admin-web-e2e.sh`.
- **Runtime, Compose, or deployment behavior changed** — `bash scripts/portability-smoke.sh`.
- **Provider or AI adapter changed** — the matching opt-in integration tests, which must skip
  cleanly without credentials. If a real end-to-end HTTP path must be proven and portability is
  not selected, run `bash scripts/smoke-api.sh`.
- **Default smoke-profile-only HTTP behavior changed and is not covered by portability plus
  pre-release runtime liveness** — `bash scripts/smoke-api.sh` (needs `CLERK_SECRET_KEY` +
  network). Do not run it merely because portability already exercised its own profile.
- **New or materially rewritten Markdown** —
  `venv/bin/python .agents/skills/document-audience/scripts/check_document_profile.py <paths>`.

Docs-only changes skip everything except the last bullet.

Long gates must never be weakened to fit a timeout: run them in the background with a retained log
and exit-status file, poll to completion, and report the recorded output.

### 4. Author missing tests

If the change adds new logic, write its tests **now**, guided by the plan's **Test Impact**
section and `docs/4-unit-tests/TESTING.md`. If no new logic was added, skip.

- Test **observable behavior** (inputs → outputs/persisted effects), never internal wiring.
- **Mock-pain tripwire**: if mock setup grows longer than the assertions, stop fighting it — take
  a seam from the testing guide, or skip the deep unit test and add one line to
  `docs/4-unit-tests/COVERAGE-DEBT.md` (`path | why hard | escape plan`).
- **Critical-path floor**: behavior touching auth, deletion, persistence, cost, or external
  request shape keeps at least one behavioral test or manual integration check. Coverage debt may
  defer internal-path depth, never safety-critical behavior.
- Never hide untested code — no coverage-ignore comments, no config exclusions, no lowered gates.

### 5. Build the summary

`lint: clean | typecheck: clean | tests: N passed (M new)`

### 5.1. Architecture sync

Before code review, read `docs/ARCHI-rules.md` in full. Compare `docs/ARCHI.md` with the actual
implementation and Stage 2 architectural decision; update it if its guidance changed. Run
`LC_ALL=C bash scripts/count-archi-tokens.sh docs/ARCHI.md`; record in the verification file
whether the guide changed and why. Sync other affected canonical docs before code review.
These files are part of the reviewed diff. A later documentation correction returns to
affected review and QA steps.

### 6. Review-entry evidence

Build `$GATE_SUMMARY` from the affected checks above, with exact outcomes and candidate identity.
Follow `shared/process/gates.md`; do not add a broad fast run merely to enter review. A red required check still
blocks review, and shared or uncertain impact requires broader relevant coverage.

---

## Codex code review

Run it after the required review-entry checks pass — no confirmation needed, including none
for sending the plan, the diff and the repository code to the external Codex service
(`adapters/TRIP/process.md` "Standing Codex-delegation authorization"). Satisfy any runtime sandbox/network
prompt yourself; never route it to the owner.

**Move the Linear issue to `Code Review`** when the loop starts. Stage 5 moves it to `Testing`
when independent QA begins, after review convergence.
No comments on either transition.

Use `codex-code-review`'s **own** wrapper scripts — they pin `STATE_DIR` themselves, so there is
no export to forget (a forgotten one silently operates on the plan-review thread for the same
target).

1. **Start** — both leading sentences are **mandatory** (`adapters/TRIP/process.md` §4.3). Without the first, the
   vendor prompt may skip "Plan conformance" entirely, because it looks for `docs/1-plans/`;
   without the second, the review can land on generic vendor criteria and a
   Critical/Major/Minor/Suggestion scale:
   ```bash
   bash .claude/skills/codex-code-review/scripts/start.sh \
       --prompt-file .claude/skills/codex-code-review/prompts/start.tpl \
       docs/work/<ID>/plan.md \
       "TARGET is the implementation plan — read it in full and evaluate the diff against it. Review criteria, severity scale (P0/P1/P2/P3) and approval gate: .claude/skills/nadili-process/review-checklist.md. $GATE_SUMMARY"
   ```
   For unplanned work (no plan file), pass a free-form label instead and drop the first sentence —
   **keep the criteria pointer**.
2. **Parse the trailing tag**: `APPROVED` → synthesize. `NEEDS_REWORK` → surface to the owner.
   `REQUEST_CHANGES` → continue.
3. **Address findings — P0/P1/P2.** Quote each with `file:line`, read the actual code, fix
   legitimate ones, and push back on incorrect ones with evidence. P2 is returned for correction
   and blocks approval. **Only P3 is deferred**: record it without a fix-up batch. A finding
   that demands unrelated scope is not a P2 under
   `shared/process/review-checklist.md`.
4. **Write implementer notes** (1-3 sentences): what you fixed, what you pushed back on and why,
   any owner decision or environment limit Codex should stop re-flagging.
   When the optional ordinary review telemetry collection is active, the manager records its
   existing assessment after this step with `shared/scripts/review_telemetry.py note --run-id ...
   --input-file ...`; use `status` to obtain the selected task's latest run ID. The bounded JSON
   contains only the assessor and validated finding conclusions. Missing notes remain unknown and
   do not alter the review gate. The ordinary review uses one Sol/high reviewer; Astra/medium is
   reserved for the separate architecture stage.
5. **Resume**. After each legitimate P0/P1/P2 fix, run affected checks under `shared/process/gates.md`.
   Expand for shared or uncertain impact; do not repeat broad checks solely because of rebase
   or another review pass. Append exact delta outcomes to `$GATE_SUMMARY` before follow-up:
   ```bash
   bash .claude/skills/codex-code-review/scripts/resume.sh \
       --prompt-file .claude/skills/codex-code-review/prompts/resume.tpl \
       --notes "Fixed X. Pushed back on Y because Z." \
       docs/work/<ID>/plan.md "$GATE_SUMMARY"
   ```
   → back to 2.
6. **Review allowance** — follow `shared/process/SKILL.md`: default two passes. Resolve P0/P1/P2 within the
   allowance or stop for an explicit owner amendment; retries do not reset the budget.

### Synthesize

Reviewer `APPROVED` permits synthesis and handoff to QA. Do not run `pre-release.sh` here;
the integration procedure runs the affected check against the final delivery commit. Candidate
qualification runs the full suite separately. Skip synthesis if the loop
converged on turn 1 and the state file already holds the full review. Otherwise produce a
consolidated review:

```bash
bash .claude/skills/codex-code-review/scripts/resume.sh \
    --prompt-file .claude/skills/codex-code-review/prompts/synthesize.tpl \
    docs/work/<ID>/plan.md "Today's date is YYYY-MM-DD"
```

It emits the `PROMOTION_READY` sentinel and leaves the ticket identity to the integration
procedure. The sentinel means the review record is ready for promotion; the integration check
still gates delivery.

If Codex review cannot run, use a separate report-only reviewer against the same checklist and
write the CR manually. A missing vendor review never skips Nadili code review.

Surface reviews verbatim. Keep edits scoped. If Codex repeats a finding, re-read carefully — you
likely addressed an adjacent concern. Reset the thread only if context is confused.

---

## Independent Backend QA and Frontend QA

After review converges, run `shared/process/SKILL.md` Stages 5–6. Move Linear to `Testing` at the start of
Stage 5. Freeze the candidate and dispatch an **independent fresh-context tester invocation**
for each applicable route. Supply the pinned spec, plan, candidate revision/diff and isolated
runtime setup, not the implementer's success summary. Do not edit the candidate worktree while
the tester is running. The tester reports findings but must not change code, commit or move
Linear. Record both applicability decisions, expected/observed scenarios, evidence and verdicts
in `docs/work/<ID>/verification.md` using `shared/process/testing.md`.

If a tester finds a defect, fix it in implementation, refresh the affected checks and Codex
review, then freeze a new candidate for affected-route retest. Repeat both routes when the fix
can affect both. A blocked applicable route blocks integration; owner UAT does not replace it.

---

## Handoff to release

After Codex converges and independent QA completes:

- Cross the corresponding checkboxes in the plan.
- Confirm the plan's `Verification:` line still matches what was actually built (`adapters/TRIP/process.md`
  §1.4.1); the release procedure applies it verbatim.
- Determine completion **mechanically, not by asking**. Complete when all four hold:
  1. every plan checkbox is crossed or explicitly recorded as out of scope / owner-only,
  2. the review-entry testing gate is green, and
  3. the independent code review has an `APPROVED` verdict with no open P0/P1/P2.
  4. Backend QA and Frontend QA passed or are justified as not applicable in the verification file.

**If all four hold**: proceed **straight into the release without a question** — follow
`shared/process/integrate.md` beside this file in the same session, passing the same plan path. Delivery is
pre-authorized and the release procedure asks nothing either (`adapters/TRIP/process.md` §6), so a question here
would be pure latency.

**If something is genuinely unresolved** — an owner-only checkbox (credentials, dashboard access,
an ops action), or a plan item you decided not to build — that is the *only* reason to stop. Move
the Linear issue to `Blocked` with one bounded comment naming the decision needed (`adapters/TRIP/process.md`
§1.4–1.5), and use `AskUserQuestion` **once**, naming the specific blockers and offering "Release
without them" / "Keep working" / the concrete decision you need. Batch every open item into that
single question; never ask a generic "is it complete?". On resume, move the issue forward from
where the fix lands — never backward.
