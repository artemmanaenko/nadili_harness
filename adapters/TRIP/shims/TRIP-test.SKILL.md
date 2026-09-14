---
name: TRIP-test
description: Write/run tests following project standards (deep test authoring)
disable-model-invocation: true
argument-hint: "component or feature to test"
---

# Testing Mode — delegated

**This file is a shim. It holds no project content on purpose.**

Nadili's testing procedure lives in
[`.claude/skills/nadili-process/testing.md`](../../../shared/process/testing.md). **Read that file and
follow it end to end.** It is complete and standalone — nothing from the vendor's original body is
needed, and anything that reappears here after a `TRIP-upgrade` is stale by definition.

In particular the vendor body ships placeholder test commands and generic priorities; ours are the
real ones, together with the concurrency rules that keep parallel agents off each other's
databases and ports.

Cross-cutting rules: [`.claude/skills/nadili-process/SKILL.md`](../../../shared/process/SKILL.md).

Test: $ARGUMENTS

---

*Why a shim:* the TRIP skills are vendor-maintained and `TRIP-upgrade` rewrites them from the
upstream template, preserving only a fixed list of named regions. Keeping the procedure here would
lose it on every upgrade. Keeping this file empty of project content means an upgrade can destroy
nothing. Restore with `venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while
a shim is missing.
