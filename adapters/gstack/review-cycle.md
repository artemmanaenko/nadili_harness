# Code review convergence

Goal: find in-scope defects in the first complete review, then verify corrections without
restarting discovery on unrelated upstream work. This contract owns gstack code-review cycles;
`shared/process/review-checklist.md` owns severity, `shared/process/SKILL.md` owns stage outcomes, and the budget ledger owns
dispatch limits. TRIP keeps its transport. A second pass may discover a real blocker; report it.

## Inputs and ownership

- Coordinator: approved spec/plan, staged candidate, pinned base commit, green gate summary,
  exact attempt/reservation IDs and bounded correction handoff.
- Runner: Git snapshots, correction packet, independent model invocation, result validation,
  private evidence history and attempt accounting. Never manually reserve or start its attempt.
- Reviewer: inspect changed behavior and direct consumers; return all supported findings,
  coverage gaps and prior-blocker dispositions. No edits or test execution.
- Implementer: fix the affected scenario and run focused regression checks before another review.

## Frozen base and integration

Resolve the base commit at review entry. Keep it through review, fixes and verification.
`origin/main` advancing does not invalidate that review and is not a P2. Do not rebase solely
to obtain another code-review approval. Never change the candidate while review/QA is running.

Stage 7 still fetches/rebases, runs the exact affected integration gate and fast-forwards main.
Inspect upstream changes, conflicts, shared dependencies, contracts and runtime configuration.
Record the old/new bases and why review/QA evidence transfers, or refresh the affected review
and runtime scenarios. Textual non-overlap alone does not prove independence. A semantic change
or unresolved impact requires fresh evidence; unrelated upstream changes alone do not require
another model review. When a model refresh is nevertheless required solely by the rebase, the
runner records it as `review-refresh` without consuming a formal round only if the prior verdict
was `APPROVED` and the runner-owned item-path delta is empty. It still inspects upstream impact;
any correction after a `base_change` finding uses an ordinary round. Validate each final candidate
through the integration entrypoint;
`shared/process/gates.md` defines when its eligible check results can be reused.

## Runner protocol

Use `shared/scripts/codex_code_review.py` with the ordinary item, spec, plan, candidate, base, gate and
attempt arguments from `adapters/gstack/orchestrator.md`. Stage all intended candidate paths first;
unstaged changes, conflicts and non-ignored untracked files fail preflight. The candidate label
is descriptive; runner-owned `review_context.tree` is the actual reviewed index tree.

The first successful verdict is stored beside the canonical budget state in `<item>.reviews/`.
Each later invocation automatically loads `latest.json`; hashed attempt filenames retain every
completed verdict, including REQUEST_CHANGES. The per-item lock prevents simultaneous reviews.
Git refs under `refs/nadili/reviews/` retain the reviewed trees across garbage collection.
Failed attempts do not advance this history. Never delete history to restart an exhausted cycle.
Completed rebase-only refresh verdicts advance history and retain attempt/usage evidence, but their
`review-refresh` reservation does not increment `review_rounds_by_scope`.
Only the runner's evidence-verifying reservation command can create this kind; the public generic
reserve command rejects it.

The runner builds the correction from the union of item paths before/after the change. After a
rebase it supplies upstream changed paths and overlapping upstream diffs separately. The reviewer
traces changed dependencies too; path filtering is transport, not permission to ignore impact.
Unrelated upstream file bodies do not consume the correction patch allowance.

- Initial review: Sol/xhigh; whole item diff and all ten checklist sections.
- Correction: Sol/medium; prior blockers, generated delta and affected behavior/dependencies.
- Broader change: `--exhaustive-reason <technical-code>` selects Sol/xhigh and retains prior
  findings. Required when the bounded packet exceeds 128 KiB or a material redesign needs a full
  pass. Omitting previous findings never authorizes a silent restart or another budget allowance.

`--previous-review-file` imports an existing report. Legacy reports also require
`--previous-tree <reviewed-index-tree>` and `--previous-base <review-base-commit>`; recover those
from recorded evidence, never guess. Missing evidence blocks import. The former
`--correction-diff-file` is optional and accepted only if it matches the generated patch;
normally omit it. Once history exists, use its latest result.

## Findings and closure

Every finding has a stable ID, priority, location, expected/actual behavior, impact and action.
Every previous P0/P1/P2 gets exactly one disposition: `fixed`, `rejected` with technical disproof,
or `open` with matching blocking finding. Missing/duplicate dispositions invalidate the result.

| Origin | Meaning |
|---|---|
| `initial_miss` | Present in the original candidate; first-pass discoveries use this too. Count as a miss only when first discovered later. |
| `fix_regression` | Introduced by a correction; cite the changed behavior. |
| `unresolved` | Previous defect remains; retain its ID. |
| `base_change` | Concrete defect caused by upstream changes; name the affected dependency. Mere base movement is not a finding. |

APPROVED requires no open P0/P1/P2, complete prior-blocker accounting and applicable green gates.
Deferred P3 does not return as a correction task. A prose checklist is not coverage: evidence
names inspected behavior, relevant consumers and failure/state transitions; gaps stay explicit.

## Correction handoff

Pass finding IDs, expected behavior, implicated state transitions and focused checks to the
implementer. Verify the scenario family, not only the cited line. Select only relevant cases:

- Navigation: direct load, click, Back/Forward, URL/hash restoration and focus after rendering.
- Async proof/submission: early submit, one completion, retry, expiry, edit/cancel/unmount,
  stale callback and provider silence.
- Durable/backend work: failure before/after commit, replay, authorization and competing work.

Return observed results, remaining gaps and changed paths. Independent QA remains required;
these checks prepare the candidate rather than replace the tester.

## Evaluation

For the next ten completed items, compare first-pass misses, fix regressions, unresolved findings,
base-change defects, failed launches and total review time. Count formal verdicts separately from
transport retries and QA discoveries; use the ledger for unavailable usage and lifecycle timing.
Keep Sol/xhigh and Sol/medium unchanged while measuring this protocol. Do not infer model quality
from a raw round count or convert cached token totals to cost without billing evidence.

Inspect the ten most recently reviewed items with:

```bash
venv/bin/python shared/scripts/codex_review_packet.py \
  --state-directory "$(git rev-parse --git-common-dir)/nadili-orchestration" --limit 10
```

This reports completed verdict counts, their elapsed time and later blocking-finding origins.
Filter to delivered items for the comparison; add failed-attempt timing and QA discoveries from
the ledger and verification records. An empty report means no new-protocol evidence yet.

## Writing guidance

Keep durable rules here and link from role prompts. Put deterministic mechanics in code and
verification in tests; add a rule only for an observed failure. These choices apply
[OpenAI's Codex best practices](https://learn.chatgpt.com/guides/best-practices): concise reusable
instructions with explicit context, constraints and completion criteria.
