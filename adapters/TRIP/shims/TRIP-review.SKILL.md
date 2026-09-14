---
name: TRIP-review
description: Review code following project standards (manual fallback/audit path)
disable-model-invocation: true
argument-hint: "version or feature to review"
---

# Review Mode — delegated

**This file is a shim. It holds no project content on purpose.**

Nadili's manual review procedure lives in
[`.claude/skills/nadili-process/review.md`](../../../shared/process/review.md). **Read that file and
follow it end to end.** It is complete and standalone — nothing from the vendor's original body is
needed, and anything that reappears here after a `TRIP-upgrade` is stale by definition.

The review criteria, severity scale and approval gate live in
[`.claude/skills/nadili-process/review-checklist.md`](../../../shared/process/review-checklist.md); the
output skeleton lives in
[`.claude/skills/nadili-process/cr-template.md`](../../../shared/process/cr-template.md).

Cross-cutting rules: [`.claude/skills/nadili-process/SKILL.md`](../../../shared/process/SKILL.md).

Review: $ARGUMENTS

---

*Why a shim:* the TRIP skills are vendor-maintained and `TRIP-upgrade` rewrites them from the
upstream template, preserving only a fixed list of named regions. Keeping the procedure here would
lose it on every upgrade. Keeping this file empty of project content means an upgrade can destroy
nothing. Restore with `venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while
a shim is missing.
