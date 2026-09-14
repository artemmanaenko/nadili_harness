---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Manual code review procedure (Nadili)

The **report-only fallback/audit path**. TRIP normally uses the Codex loop inside `adapters/TRIP/implement.md`.
For gstack items in Codex, `shared/scripts/codex_code_review.py` launches the independent noninteractive
reviewer. Its initial pass is an exhaustive Sol/xhigh review; its correction pass is a focused
Sol/medium review of the prior findings and bounded fix patch. `adapters/gstack/orchestrator.md` is canonical
for Stage 4 invocation; `adapters/gstack/review-cycle.md` owns generated correction packets and closure. Use this procedure for a manual fallback,
a past-item audit, or unplanned work.

This file lives in `nadili-process/` and is **never rewritten by `TRIP-upgrade`**. The vendor's
`.claude/skills/TRIP-review/SKILL.md` is a shim pointing here, enforced by
`scripts/check_trip_shims.py`.

## Read first

1. `docs/ARCHI.md` — verify architectural compliance.
2. `AGENTS.md` — canonical architecture and security invariants (wins over `ARCHI.md` on conflict).
3. `docs/coding-standards.md` — the style and error-handling rules the review enforces.
4. The related plan — `docs/work/<NAD-ID>/plan.md` (current convention; older plans are in
   `docs/1-plans/`).
5. The related ticket changelog in `docs/2-changelog/`, if already drafted; Stage 7 may create it later.
6. `shared/process/review-checklist.md` beside this file — **single source of truth** for review criteria,
   severity classification, and the approval gate.

---

## Apply the checklist

Walk every section of `shared/process/review-checklist.md` against the change. Tick passing items. Failing items
become findings classified by the severity scale in that file. Approval requires the gate at the
bottom of it.

**P0, P1 and P2** are fixed (`shared/process/review-checklist.md` § Fix Policy); only P3 may remain deferred.

Do not copy the checklist into output — link to it.

---

## Create the review file

Save to `docs/3-code-review/CR_NAD-<ID>.md` (the ticket id; tickets carry no version).

Render the skeleton from `shared/process/cr-template.md` beside this file:

1. Copy the markdown block from that file.
2. Replace every `<angle-bracket placeholder>` with concrete content.
3. Tick `[x]` for passing checklist items; leave unchecked with a one-line caveat otherwise.

Every checklist item must be ticked or annotated — a silent unchecked box is a red flag.
