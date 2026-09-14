---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Integration procedure (Nadili)

The TRIP implementation of `shared/process/SKILL.md` Stage 7. `/TRIP-3-release` delegates here. This file is
project-owned and is never rewritten by `TRIP-upgrade`.

Runs after implementation has converged (review-entry gate green, independent code review
approved). Normally chained from the implement procedure in the same session; may be invoked
standalone.

A ticket **integrates**; it does not release. It records its code-review and changelog evidence,
passes the affected integration check, and fast-forwards `main`. It receives **no version and no
tag**. Versions, tags, the full qualification suite and signed evidence belong to one immutable
candidate coordinated by `production-release.md`.

The main process in `shared/process/SKILL.md` wins. Legacy TRIP details live in
`adapters/TRIP/process.md`: §1 Linear lifecycle, §2 worktrees, §6 delivery authorization,
§7 versions belong to candidates.

---

## Prerequisites

- Implementation complete.
- Applicable Backend QA and Frontend QA verdicts passed; inapplicable routes have reasons in
  `docs/work/<ID>/verification.md`.
- Review-entry gate green.
- Independent code review converged (`APPROVED`) with no open P0/P1/P2.

## Candidate and activation boundary

`production-release.md` and `bash scripts/release-candidate.sh` are the only place a version is
reserved, the aggregate artifacts are stamped, the full suite runs, a tag is published and signed
evidence exists. Ticket integration never does any of those, and never runs the qualification
suite.

The activation receipt is a **deploy** precondition: the installed host consumes it on forward
admission (`deploy-release`, the forward branch of `prod-deploy.sh`), and `install-payload`
carries it across an authority change. It is never a precondition for preparing, qualifying or
publishing a candidate, and never for integrating a ticket.

### Standalone verification (fresh session, not chained)

This file assumes you are already standing in the item's worktree. If not — invoked fresh, not
chained from `adapters/TRIP/implement.md` — re-enter it first (idempotent, resumes at its current tip):

```bash
bash shared/scripts/worktree-new.sh <ID>
cd .codex/worktrees/<id-lower>
```

Rebase onto the current remote first — `origin/main` moves continuously while other agents
integrate, and that is expected:

```bash
git -C <worktree-path> fetch origin && git -C <worktree-path> rebase origin/main
```

Then verify (backend commands; add the web ones only if TS/TSX changed). The exact repository
Ruff, format, mypy, and boundary-check commands are owned by `bash shared/scripts/pre-commit.sh`; use it
as the authority instead of duplicating those command blocks here:

```bash
source venv/bin/activate
python -m pytest <paths-from-the-plan's-Test-Impact-section> -m "not integration" -q

corepack pnpm --filter <affected-package> lint
corepack pnpm --filter <affected-package> typecheck
corepack pnpm --filter <affected-package> test
```

If the change touched API schemas or operation IDs, confirm the contract is in sync:

```bash
venv/bin/python scripts/export_openapi.py --check
venv/bin/python scripts/check_openapi_contract.py
```

Also verify the completed code review record exists (Step 3). If Codex supplied the review,
confirm its state file too. Do not infer approval from a missing vendor state file.

Any failure blocks integration — fix it, or return to the implement procedure first.

---

## Step 1: Date and project week

```bash
date '+%d-%m-%Y %H:%M' && python3 -c "from datetime import date; print('Project week:', (date.today() - date.fromisoformat('2026-08-10')).days // 7 + 1)"
```

Week anchor: the Monday of the week TRIP Init was run. Python rather than `date -d`, which macOS
BSD `date` lacks. `scripts/release_stamp.py` carries the same anchor for candidate stamping; a
test pins the two against each other.

## Step 2: Draft ticket artifacts

Keep the reviewed candidate and pinned base while drafting artifacts. Commit the explicit item
paths in Step 10, then fetch/rebase once at the final integration boundary in Step 11. Do not
add a preliminary autostash/rebase or repeat checks merely to write delivery documents.

Verify the Stage 4 review artifact and draft the ticket changelog in the Step 3–5 order,
named by the **ticket**, never by a version: `docs/3-code-review/CR_NAD-<ID>.md` and
`docs/2-changelog/NAD-<ID>.md`. There is no
`x.y.z`, no `vNEXT`, no reservation. The artifacts may record review-entry evidence, but must not
claim the integration-check result before Step 11 runs it.

## Step 3: Verify the code review

For gstack, apply `adapters/gstack/review-cycle.md` when rebasing: record upstream impact and either justified
evidence transfer or the affected refresh. A newer base alone is not a stale verdict. Its runner
history lives beside the canonical budget state; the TRIP state-key instructions below apply only
to the TRIP transport.

1. Read the Stage 4 `docs/3-code-review/CR_NAD-<ID>.md` and confirm its verdict covers the
   current candidate, names no open P0/P1/P2, and contains no unfinished placeholders or
   `PROMOTION_READY`. If it is missing or stale, return to Stage 4 before delivery.
2. If Codex supplied the review, compute its state file path. **Derive the key with `key.sh`, never inline.** `target_key()`
   appends a checksum suffix to the sanitized path, so a hand-rolled `realpath | sed` produces a
   name that does not exist and this step fails to find a review that is sitting right there:
   ```bash
   STATE_KEY="$(bash .claude/skills/codex-plan-review/scripts/key.sh <plan-path>)"
   STATE_FILE=".claude/skills/codex-code-review/state/${STATE_KEY}.review.txt"
   ```
3. Confirm a Codex review state holds the final reviewed result (`PROMOTION_READY` for a
   synthesized multi-round review, or the full review on turn 1). If another independent
   reviewer supplied the CR, its recorded evidence and verdict stand without a Codex state.
   Do not rewrite an approved CR during delivery just to change its source.

## Step 4: Commit message

Propose a one-line commit message in the repository's normal format, with the ticket explicit:

```text
feat(NAD-<ID>): <short description>
```

## Step 5: Changelog file

Create `docs/2-changelog/NAD-<ID>.md`:

```markdown
# Changelog - NAD-<ID>

**Integrated**: Week a, DD-MM-YYYY at HH:MM
**Ticket**: NAD-<ID>
**Object**: the commit message
**Code review**: `docs/3-code-review/CR_NAD-<ID>.md` (reviewer and verdict)

## Changes

[Describe what changed]
```

`scripts/release_stamp.py` parses **exactly one** `**Object**: ` line per ticket changelog when a
candidate is prepared — keep it on one line, factual, and unique in the file.

## Step 6: Candidate-only aggregate artifacts

There is no ticket step here. `docs/2-changelog/changelog_table.md` (row and summary), the
README's "Current published release" line and `pyproject.toml`'s `version` are stamped only by
`release-candidate.sh prepare` through `scripts/release_stamp.py`. A ticket **must not** edit
them. This also removes the repository's top rebase-conflict target from every ticket.

## Step 7: Architecture update

Stage 4 of `shared/process/SKILL.md` already owns the architecture update **before code review**. Confirm
that the reviewed `docs/ARCHI.md` still matches the actual diff. If a correction is required
here, return to the affected verification and review steps before delivery.

1. Read `docs/ARCHI-rules.md` in full.
2. Update `docs/ARCHI.md` following those rules if needed.
3. `LC_ALL=C bash scripts/count-archi-tokens.sh docs/ARCHI.md`

`LC_ALL=C` is required because macOS `awk` can otherwise fail its
multibyte conversion and print a false `~0 tokens` result for valid UTF-8 punctuation.

The estimator exits nonzero above 10,000 tokens. Compact the agent guide before committing;
an over-budget result blocks integration.

## Step 8: README

No version step. The README's release line is candidate-owned (Step 6). Content corrections
belong to Step 9 — do not sync sections here.

## Step 9: Documentation sync

Keep the pre-existing (non-TRIP) documentation aligned with the code:

1. Read the plan's **Documentation Impact** section.
2. Contrast it with the **actual diff**
   (`git -C <worktree-path> fetch origin && git -C <worktree-path> diff origin/main...HEAD`) — the
   plan may have fallen short; a doc affected by the real change must be synced even if the plan
   did not list it.
3. If any affected document still needs an update, return to Stage 4 and refresh the affected
   review and QA evidence before this delivery step. **Factual corrections only**: commands, paths, build targets,
   script/table entries, cadences, config/env vars, structure trees. It is **forbidden** to touch
   voice, tone, or strategic/editorial content.
4. Updated files ride in the integration commit (Step 10) — never a separate commit.

Project rules:

- Product-behavior changes must update the canonical PRD (`PRD.md` or `docs/web/PRD.md`) in this
  same change. **Replace** superseded behavior; do not preserve it as history.
- Never hand-edit generated artifacts (`packages/contracts/openapi.json`, `requirements*.txt`).
  If stale, regenerate (`scripts/export_openapi.py`, `bash scripts/lock.sh`).
- New or materially rewritten Markdown needs `document_profile` front matter and must pass
  `venv/bin/python .agents/skills/document-audience/scripts/check_document_profile.py <paths>`.
- Keep `AGENTS.md` under 300 lines and non-duplicative.

If the plan says "None" and the diff confirms it, skip with a one-line note.

---

## Delivery is automatic — never ask

Delivery is pre-authorized by the repository owner (`adapters/TRIP/process.md` §6, `AGENTS.md`). Run Steps 10-14
straight through and **never** call `AskUserQuestion` in this procedure — not before the commit,
not before the push, not in any collapsed form.

The confirmation protects nothing: independent review returned `APPROVED` with no open P0/P1/P2, the implement
procedure already stopped if anything was unresolved, and Step 11 blocks the push until the final
commit passes the affected integration check. A prompt that can only be answered "yes" is
latency, not safety.

Binding regardless — reported, never confirmed away:

- never `git commit --no-verify`; a red gate blocks integration
- never force-push; on rejection re-fetch, rebase, retry
- fast-forward only; never a merge commit
- never integrate on a red gate, an open P0/P1/P2, or a `NEEDS_REWORK` verdict
- never run the candidate qualification suite for a ticket, and never create a tag

**One carve-out**: if the plan itself records an explicit owner-confirmation requirement for
integration (destructive migration, public-contract break, or anything flagged at plan time),
honor it. Absent such a line, deliver.

## Step 10: Commit

Stage explicit paths — the implementation, the two ticket artifacts, and every document synced in
Steps 7–9:

```bash
git -C <worktree-path> add <explicit paths for this item>   # never `git add -A` — SKILL.md Stage 3
git -C <worktree-path> commit -m "<commit message from Step 4>"
```

Only the commit message. No `Co-Authored-By`, no other trailer. Never `--no-verify`. Nothing is
stamped: no version, no reservation, no aggregate row.

## Step 11: Final rebase and affected integration check

Rebase the integration commit onto the current remote and run the affected integration check
against the exact merge-base-to-commit range to be delivered:

```bash
git -C <worktree-path> fetch origin && git -C <worktree-path> rebase origin/main
bash shared/scripts/integration-check.sh --base "$(git -C <worktree-path> merge-base origin/main HEAD)" --head HEAD
```

`shared/scripts/integration-check.sh` owns the affected capability union. For an exact `--base/--head`
range, it runs the bounded affected capabilities, including PostgreSQL for backend changes;
Admin E2E, runtime liveness, public visual validation and portability are candidate-qualification stages, and the check prints
each deferred capability with its reason. The deliberate local `--dirty` and `--staged` scopes retain the full control-plane expansion, while
`--dev` executes the affected dirty-scope union. Never substitute a local convenience
check, and never run the candidate qualification suite here — `production-release.md` runs it once
per candidate.

On a red check: fix, commit the fix, rebase, and re-run. If the fix changes reviewed code, tests,
runtime configuration, or canonical documentation, first run affected micro-gates and return to
the same reviewer; re-synthesize the review after renewed `APPROVED`. Never push past a red check.
For gstack, use the generated correction packet and closure contract in `adapters/gstack/review-cycle.md`;
mechanical derived-evidence corrections need reference validation, not another model verdict.

After an amend, invoke the affected integration check for the new final candidate before pushing.
The runner may reuse eligible identical-input results under `shared/process/gates.md`; a changed commit label
alone does not force repeated execution. Runtime checks remain fresh.

The issue entered `Testing` when independent QA began under `shared/process/SKILL.md` Stage 5. After the
check is green, confirm it is still there; do not repeat the transition or post a comment.

## Step 12: Fast-forward `main`

**In a worktree** (the default — under `.codex/worktrees/<item>` on `work/<ITEM-ID>`): `main` is
checked out in the primary repo, so `git checkout main` **will fail**. Expected. Push directly —
after the rebase this is a fast-forward by construction:

```bash
git -C <worktree-path> push origin HEAD:main
```

**In the primary checkout on a `feat/…` branch**:

```bash
git checkout main
git merge --ff-only origin/main
git merge --ff-only <feature-branch>
git branch -d <feature-branch>
git push origin main
```

If the push is rejected, `origin/main` moved again: re-fetch, rebase, return to Step 11 and re-run
the check on the rebased tree, then retry. **Never create a merge commit and never force-push.**
Green evidence must always belong to the exact HEAD being pushed. No tag is created; nothing else
is pushed from this procedure.

## Step 13: Close Linear

Once `origin/main` has the commit, move the issue to its **terminal status** and post the **one**
delivery comment (`adapters/TRIP/process.md` §1.4–1.5). There is no reservation to release — a ticket holds none.

The terminal status is **`UAT`** or **`Done`**, per `adapters/TRIP/process.md` §1.4.1 — read it if you have not:

1. **Read the plan's `Verification:` line** (`docs/work/<ID>/plan.md`). It was decided at TRIP-1
   and is applied here verbatim. Do not re-argue it because the diff turned out bigger or smaller
   than planned.
2. **Only if the plan has no `Verification:` line** — a legacy plan, a hotfix, or an item that
   skipped TRIP-1 — decide it here with §1.4.1's single question: *does a human looking at the
   running product change the verdict?* State the classification and its one-line reason in the
   Step 15 report. This is the exception, not the normal path: the decision belongs to planning,
   where the work's user-visible surface is the subject of the conversation rather than one signal
   among a diff, a gate log and a code review.
3. `UAT` only for owner-verifiable work: UI, UX flow, copy, feel — something the owner will look
   at and judge. `Done` for agent-verified work: backend logic, filters, refactors, tests,
   harness, infra, docs, performance, SEO/metadata. Mixed items follow their user-visible part.

**Do not park agent-verified work in `UAT`.** It blocks nothing, but it pollutes the owner's real
verification queue and the rolled-up status of the parent epic and project — that is the failure
this rule exists to prevent.

`Done` set here applies **only to the item just integrated**. It means integrated and verified,
never deployed. Never set `Done` on any other item, never "tidy up" an existing `UAT` item, and
never close someone else's work.

The comment is a verification brief, not a receipt. Its first line is exactly
`Integrated on main — <commit message> (<short sha>)`. Then, for `UAT`, 2-4 bullets on what to
check in user-visible terms, naming where to see it; for `Done`, one line on why no human check
applies and the one symptom a regression would show. Then the CR and changelog paths. No diffs,
no logs, no test output. A failed Linear write is reported in one line and does not affect the
integration.

## Step 14: Remove the worktree

Step 14 intentionally targets the primary repo, not the worktree — that part is not a mistake.
Use the harness cleanup script so the primary `main` is fast-forwarded before branch deletion and
a plan-only upstream (`origin/work/<ITEM-ID>`) cannot make `git branch -d` reject an already
integrated branch:

```bash
bash <worktree-path>/.claude/skills/nadili-process/scripts/release-cleanup.sh \
  <primary-repo-root> <worktree-path> <ITEM-ID>
```

The script fails closed unless the target is the expected clean item worktree, the primary checkout
is on a clean tracked `main`, and both the item branch and local `main` are ancestors of
`origin/main`. If it refuses because of leftovers or divergent primary state, inspect and report;
never force cleanup through the script. Skip entirely if the integration ran in the primary
checkout.

## Step 15: Report

Delivery ran without asking, so this report is how the owner learns what happened. A few lines:

- the commit (short sha) and the one-line commit message
- `docs/3-code-review/CR_NAD-<ID>.md` and `docs/2-changelog/NAD-<ID>.md`
- the integration-check command and its result
- the Linear issue and its new terminal status (`NAD-XXX → UAT` or `NAD-XXX → Done`), and — if
  the plan carried no `Verification:` line — the one-line reason for the classification
- the worktree and branch removed
- anything that needed a rebase mid-integration, or any Linear write that failed

No diffs, no gate logs — those live in git and in the CR.
