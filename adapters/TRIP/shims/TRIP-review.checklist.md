# Code Review Checklist — moved

**STOP. The criteria are not in this file. Read
`shared/process/review-checklist.md` and apply that file instead.**

It is the single source of truth for the review checklist, the severity classification, and the
approval gate. Everything a review needs is there and nowhere else:

- the 10 checklist sections (functional, code quality, architecture, tenancy/public boundary,
  contract & data integrity, jobs/pipeline/AI safety, concurrency & delivery hygiene, error
  handling, security, performance & cost);
- the **P0/P1/P2/P3** severity scale — the only severity vocabulary in this project. Do **not**
  use Critical/Major/Minor/Suggestion;
- the **P0/P1/P2 fix policy**: P0/P1/P2 are resolved, only P3 may remain deferred, and
  `APPROVED` requires no open P0/P1/P2;
- the approval gate.

If you cannot read that file, say so explicitly and stop — do not fall back to generic review
criteria or a generic severity scale.

---

*Why a shim:* this path is vendor-maintained and `TRIP-upgrade` rewrites it from the upstream
template, which ships generic criteria and a Critical/Major/Minor/Suggestion scale. Keeping our
criteria here would lose them on every upgrade, silently downgrading every review that followed.
Keeping this file empty of project content means an upgrade can destroy nothing. Restore with
`venv/bin/python scripts/check_trip_shims.py --write`; the gate fails while a shim is missing.
