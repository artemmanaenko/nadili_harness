---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Code review output template (Nadili)

The **single source of truth** for the markdown skeleton of a code review record. Every review
surface produces output conforming to this skeleton:

- the manual `shared/process/review.md` flow renders it into `docs/3-code-review/CR_NAD-<ID>.md`;
- the Codex loop's `synthesize.tpl` step renders the same skeleton, which the release procedure
  then promotes to the same path.
- the gstack adapter renders its reviewed diff and findings into the same path.

This file lives in `nadili-process/` and is **never rewritten by `TRIP-upgrade`**. The vendor's
`.claude/skills/TRIP-review/cr-template.md` is a shim pointing here, enforced by
`scripts/check_trip_shims.py`.

Anything review-surface-specific (file naming, save location, iteration-loop sentinels, "where do
findings come from") lives in the consuming procedure or prompt, **not here**.

Angle-bracket placeholders (`<like this>`) are filling-in instructions and must be replaced with
concrete content before the file is committed.

---

```markdown
# Code Review: <feature or change name>

**Review Date**: <YYYY-MM-DD>
**Ticket**: NAD-<ID>
**Files Reviewed**: <bullet list of paths from the change set>
**Plan**: <plan path — `docs/work/<NAD-ID>/plan.md` for current items, `docs/1-plans/F_*.plan.md` for pre-2026-08 ones, `docs/work/<NAD-ID>/hotfix.md` for hotfix items, or "no plan — unplanned change">

---

## Executive Summary

<1-3 sentences: what was changed and why. End with the verdict line: APPROVED / APPROVED with observations / NEEDS REVISION>

---

## Changes Overview

<2-4 sentences: scope of the change, key files, key behavior introduced>

---

## Findings

Severity tags are defined in `shared/process/review-checklist.md`.
**P0, P1 and P2** are resolved inside the Nadili item; only P3 may remain deferred.
For gstack, retain each finding's stable ID, origin and final evidence-backed disposition from
the structured review history. Distinguish review misses, fix regressions, QA discoveries and
upstream impact; a round count alone is not a defect count. See `adapters/gstack/review-cycle.md`.

### P0 — Blocking

<For each P0: short title, file:line, description, and disposition (addressed / rejected as incorrect with evidence). A P0 may not be left open. If none, write "None.">

### P1 — Blocking

<Same format as P0. A P1 may not be left open. If none, write "None.">

### P2 — Blocking

<Same format as P1. A P2 may not be left open. If none, write "None.">

### P3 — Deferred (not fixed)

<Same format, disposition "open — deferred (P3)". If none, write "None.">

---

## Checklist

State each section's outcome (passed / passed with caveats / not applicable). One line per section, no expanded prose unless a caveat needs explanation. The section names match the criteria headings in `shared/process/review-checklist.md` so a reader can cross-reference what was checked.

- [ ] 1. Functional Requirements — <outcome>
- [ ] 2. Code Quality — <outcome>
- [ ] 3. Architectural Compliance — <outcome>
- [ ] 4. Tenancy & Public/Private Boundary — <outcome>
- [ ] 5. Contract & Data Integrity — <outcome>
- [ ] 6. Jobs, Pipeline & AI Safety — <outcome>
- [ ] 7. Concurrency & Delivery Hygiene — <outcome>
- [ ] 8. Error Handling — <outcome>
- [ ] 9. Security — <outcome>
- [ ] 10. Performance & Cost — <outcome>

Tick the box (`[x]`) for sections that passed cleanly. Leave unchecked with a one-line caveat for sections with open observations.

---

## Verdict

**<APPROVED / APPROVED with observations / NEEDS REVISION>**

`APPROVED` requires zero open P0/P1/P2. Open P3 findings do **not** downgrade the verdict — use
`APPROVED with observations` when P3 observations remain.

<Final paragraph: anything a future reader should know — evidence for rejected findings, deferred P3 worth promoting to a Linear item, follow-up work, etc.>
```
