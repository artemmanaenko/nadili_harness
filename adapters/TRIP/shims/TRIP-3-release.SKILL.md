---
name: TRIP-3-release
description: Release a completed implementation - version, code review promotion, changelogs, docs, commit, tag, ff-merge, push
argument-hint: "plan file or feature label"
---

# Release Mode — delegated

**This file is a shim. It holds no project content on purpose.**

Nadili's release procedure lives in
[`.claude/skills/nadili-process/release.md`](../../../shared/process/integrate.md). **Read that file and
follow it end to end.** It is complete and standalone — nothing from the vendor's original body is
needed, and anything that reappears here after a `TRIP-upgrade` is stale by definition.

In particular the vendor body reintroduces two delivery-confirmation prompts and plan-time version
numbering; both are removed deliberately (`SKILL.md` §6, §7).

Cross-cutting rules: [`.claude/skills/nadili-process/SKILL.md`](../../../shared/process/SKILL.md).

Release: $ARGUMENTS

---

*Why a shim:* the TRIP skills are vendor-maintained and `TRIP-upgrade` rewrites them from the
upstream template, preserving only a fixed list of named regions. Keeping the procedure here would
lose it on every upgrade. Keeping this file empty of project content means an upgrade can destroy
nothing. Restore with `venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while
a shim is missing.
