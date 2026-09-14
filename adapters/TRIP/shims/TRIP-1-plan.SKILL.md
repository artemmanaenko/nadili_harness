---
name: TRIP-1-plan
description: Plan a new feature following project standards
argument-hint: "describe the feature you want to build (add --speedrun or --yolo to chain straight into implementation)"
---

# Planning Mode — delegated

**This file is a shim. It holds no project content on purpose.**

Nadili's planning procedure lives in
[`.claude/skills/nadili-process/plan.md`](../plan.md). **Read that file and follow
it end to end.** It is complete and standalone — nothing from the vendor's original body is
needed, and anything that reappears here after a `TRIP-upgrade` is stale by definition.

Cross-cutting rules: [`.claude/skills/nadili-process/SKILL.md`](../../../shared/process/SKILL.md).

Plan the following feature: $ARGUMENTS

---

*Why a shim:* the TRIP skills are vendor-maintained and `TRIP-upgrade` rewrites them from the
upstream template, preserving only a fixed list of named regions. Keeping the procedure here would
lose it on every upgrade. Keeping this file empty of project content means an upgrade can destroy
nothing. Restore with `venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while
a shim is missing.
