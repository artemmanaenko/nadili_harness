---
name: nadili-review
description: Review a Nadili implementation against its approved spec and plan, producing a report-only P0/P1/P2-gated code review before independent QA.
---

# Nadili code review

For a hotfix item, read `docs/work/<ID>/hotfix.md` as the scope/acceptance contract instead
of a spec and plan; pass `--hotfix` to the same review runner. All review gates still apply.

Read `AGENTS.md`, `shared/process/SKILL.md` Stage 4,
`shared/process/review-checklist.md` and the item plan. Reconcile the implemented
architecture with `docs/ARCHI.md` under `docs/ARCHI-rules.md` before the review-entry gate.
Use the selected adapter's **compatible report-only** review method. For gstack, apply its
scope-drift, plan-completion and critical-pass criteria without invoking installed `review`:
that skill is fix-first and edits the candidate worktree.

For gstack items in Codex, run Stage 4 through `shared/scripts/codex_code_review.py` as specified in
`adapters/gstack/orchestrator.md`. It launches a fresh minimal `codex exec` reviewer and validates its
structured P0-P3 evidence. Follow `adapters/gstack/review-cycle.md` for
frozen bases, runner-generated correction packets and prior-finding closure. Do not substitute the native `reviewer` agent: that role is reserved
for Stage 1 plan lenses. Each correction round is another one-shot run; the runner loads its previous
structured result automatically.

Produce `docs/3-code-review/CR_NAD-<ID>.md` using the Nadili template. Report P0/P1/P2 for the
implementer to fix; record and defer P3. A code or architecture correction invalidates the
affected prior verdict and checks. Move Linear to `Code Review` when review begins, then hand
the converged candidate to QA; do not integrate here.
