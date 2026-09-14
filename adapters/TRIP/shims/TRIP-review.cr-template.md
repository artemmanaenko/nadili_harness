# Code Review Output Template — moved

**STOP. The template is not in this file. Read
`shared/process/cr-template.md` and render that skeleton instead.**

It is the single source of truth for the markdown skeleton of a code review record, including the
**P0/P1/P2/P3** finding sections and the 10-section checklist rollup that matches
`shared/process/review-checklist.md`.

If you cannot read that file, say so explicitly and stop — do not invent a skeleton or fall back
to a generic Critical/Major/Minor/Suggestion structure.

---

*Why a shim:* this path is vendor-maintained and `TRIP-upgrade` rewrites it from the upstream
template, which ships Critical/Major/Minor/Suggestion finding sections and a 6-section checklist.
Keeping our template here would lose it on every upgrade. Keeping this file empty of project
content means an upgrade can destroy nothing. Restore with
`venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while a shim is missing.
