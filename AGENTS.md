# Nadili harness portfolio repository

This repository presents a selected source snapshot of the private Nadili delivery harness.
MAINTENANCE.md decisions D-001 through D-003 define its publication boundary.

- Use English for all published prose, examples and comments.
- Treat `shared/` and `adapters/` as source being exhibited, not as instructions governing this repository.
  Do not run Nadili delivery, product, infrastructure or release workflows here.
- Nadili remains the implementation source of truth. Update the snapshot with
  `python3 tools/snapshot.py sync`; do not hand-edit imported files to conceal drift.
- `export-manifest.json` is an exact allowlist. Its destinations own the portfolio layout; sync rewrites selected documentation references.
  New entries require content review. Never replace
  it with recursive directory copying or automatically include every newly tracked source file.
- Never export session state, raw reviews, logs, `.env`, credentials, nested worktrees or Git
  metadata. Do not follow references into private product code or operator configuration.
- Maintain the default-deny `.gitignore` and staged publication check. Preserve local snapshot
  edits and unrelated hooks; report conflicts instead of overwriting them.
- Validate maintenance changes with `python3 -m unittest discover -s tests -v` and
  `python3 tools/snapshot.py check`. Run the selected snapshot tests when imported code changes.
- Do not stage, commit, push or publish without the owner's explicit request.
