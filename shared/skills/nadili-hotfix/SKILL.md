---
name: nadili-hotfix
description: Finish an existing Nadili patch or concrete bounded fix through review, QA and integration into main, without retrospective planning.
---

# Nadili hotfix

Use when the owner asks to finish or integrate a concrete change, often already prototyped in
chat. An unfinished patch is a candidate, not proof of correctness. Discussion alone does not
start delivery. Respect explicit user scope and authorization; do not add another approval or
implementation-start request when the owner already asked to complete this work.

This is the hotfix entrypoint of `shared/process/SKILL.md`. It replaces planned
intake and Stages 1–2 with the bounded contract below, then uses Stages 3–7 for completion,
independent verification and integration. Production release stays with `nadili-release` and
requires its own user request. Do not create a retrospective spec, plan or plan-review ceremony.

## Establish the candidate

Read `AGENTS.md` and inspect only the relevant conversation decisions, diff, direct consumers
and focused authorities. Establish the expected result and what is still missing. Ask only for
a material missing decision or ambiguous patch ownership; do not rediscover settled scope.

Reuse the issue and its worktree when present. Check an existing issue's `blockedBy` before
proceeding, under the normal process blocker rule. For new work, register one bounded issue
in Linear team NADILI/project NADILI MVP, initially Specifying, then create `work/<ID>` with
`bash shared/scripts/worktree-new.sh <ID>`. Inspect registered worktrees before calling the creation
script; resume an existing matching worktree in place. Failed Linear writes follow AGENTS.md;
never invent an issue identifier when creation fails.

When importing existing work, capture its source checkout/base, selected commits or hunks,
and relevant new files. Preserve that recoverable source while transferring only this item's
changes into the worktree. Compare the resulting diff and file contents with the captured
candidate before editing further. Do not sweep unrelated changes, copy credentials, reset a
shared checkout or erase the source patch to make a gate pass. Source cleanup may remove only
owned changes verified unchanged since capture; preserve overlapping/newer edits and report
any deferred cleanup. Reuse a matching item worktree without copying it back through main.

## Record the contract

Write `docs/work/<ID>/hotfix.md` from the established owner intent. Use this front matter:

```yaml
document_profile: agent-primary
canonicality: derived
owner: workflow
workflow: hotfix-v1
item: NAD-123
```

Use nonempty `## Outcome`, `## Authorization`, `## Scope`, `## Origin`, `## Risks`, and
`## Validation` sections. Keep them concise:

- Outcome and Scope: observable acceptance result, bounded change and exclusions.
- Authorization: actual owner request covering completion/integration; never invent approval.
- Origin: source/base and selected patch/new files, or state that the bounded fix starts here.
  Record `Workflow adapter: gstack`, `Verification: UAT|Done` with a reason, and release type.
- Risks: affected security, data, compatibility or concurrency boundaries and any concrete
  architecture decision/no-impact conclusion. Risk determines verification depth, not planning.
- Validation: runnable acceptance/failure checks and separate Backend QA/Frontend QA routes,
  with boundary, oracle and environment (plus viewport where relevant), or justified N/A.

Include `### Interleaving` coverage when `shared/scripts/check_plan_interleaving.py` requires it,
using the existing resource/table contract in the process plan reference. The same checker
reads hotfix contracts. Validate the document profile and run
`venv/bin/python shared/scripts/check_item_ready.py <ID>` before moving the issue to In Progress.
The brief fixes scope; it does not require separate document approval or plan review.

An existing approved plan keeps its workflow. An explicit request to use hotfix for that item
authorizes migration; do not reconfirm it. Retain the prior plan in Git history, record the
decision in Authorization, and remove the active
`plan.md` as part of that recorded migration. Both active contracts are an error, not a choice
for the agent to resolve silently. Carry forward applicable decisions, evidence and the same
budget ledger. A hotfix brief cannot authorize bypassing a blocker or required gate.

## Complete and integrate

Continue in the item worktree using the shared Stage 3–7 obligations, loading each stage when
needed. For hotfix, references there to the spec/approved plan mean this contract. Do not enter
Ready for Development or wait for a second start. The coordinator may finish a small delta
directly; delegate a bounded implementation only when useful. Use the gstack adapter's review
and QA methods, not its plan-review lenses. In Codex keep `adapters/gstack/orchestrator.md` accounting,
review limits and fresh independent QA; scope is not a new budget allowance.

Fix concrete gaps in the stated behavior; update affected tests, generated contracts and
canonical docs. Record verification and review evidence in their usual locations. For formal
review use `shared/scripts/codex_code_review.py --hotfix docs/work/<ID>/hotfix.md` instead of
`--spec`/`--plan`, with the ordinary candidate/base, gate-summary and ledger arguments.
Reviewers enforce the existing P0–P3 criteria against this result. Improvements without a
concrete in-scope defect stay outside this item.

If a new product/architecture decision is necessary, preserve the candidate and ask that
specific question. Resume within this route when it is resolved; use planning only when the
owner chooses to redefine the task. High risk alone does not force retrospective planning.
Applicable QA blockers, unresolved P0/P1/P2, exhausted model/review allowance and red gates
retain their existing stop rules. No stale verdict may be used after a relevant correction.

Finish through `nadili-deliver`: verify-enabled commit hook, exact affected integration gate
and evidence rules in `shared/process/gates.md`,
fast-forward main, bounded Linear update and guarded cleanup. Report delivered commit,
verification and UAT/Done; do not claim production deployment from integration.
