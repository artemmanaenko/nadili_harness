# Repository instructions

Public portfolio of the Nadili delivery harness. Maintain the exhibit here;
`shared/` and `adapters/` contain exhibited instructions, not this repository's workflow.
Do not run product delivery, infrastructure or release workflows here.

## Where to edit

- Edit portfolio copy, diagrams and maintenance tooling here. Use `FILE_GUIDE.md` to locate files.
- For imported files, change and commit the private Nadili source first, then sync.
  `export-manifest.json` owns the exact allowlist, destination paths and substitutions;
  `snapshot.lock.json` records the source revision and exported hashes.
- Do not hand-edit imported files or lock hashes to conceal drift. If sync reports local edits
  or a substitution mismatch, reconcile the conflict; do not overwrite changes or weaken checks.

## Publication rules

- Publish English only. Keep document-profile metadata out of published prose;
  preserve native skill manifests and fenced examples.
- Export only reviewed files from committed source. Never copy source history, uncommitted or
  ignored files, product runtime code, release procedures, application prompts, real evaluation
  data, sessions, raw reviews, logs, credentials, `.env`, operator configuration or nested worktrees.
- Keep the exact allowlist and default-deny `.gitignore`; never replace them with recursive copying.
  Review new exports for private content. Missing private references are deliberate omissions,
  not permission to import more files. Keep fixtures synthetic.
- Register portfolio-owned additions in `ROOT_FILES` in `tools/snapshot.py`; imported additions
  belong in `export-manifest.json`. Keep generated ignore rules consistent with both.
- Preserve unrelated edits. Stage, commit and push only when requested by the owner.
  Keep the publication hook enabled; never use `--no-verify`.

## Setup and sync

Requires Python 3.12+ and Git. Run commands from this repository's root.
After cloning, install the publication hook, preserving any unrelated existing hooks:

```sh
python3 tools/snapshot.py install-hook
```

To refresh the snapshot from a committed source checkout:

```sh
python3 tools/snapshot.py sync --source /path/to/nadili
```

Without `--source`, sync uses the sibling `../nadili`. It does not modify the source repository
or stage, commit or push. Read `tools/snapshot.py` and `tests/test_snapshot.py` when changing sync.

## Verify changes

- After edits: run `python3 tools/snapshot.py check` and `git diff HEAD --check`.
- For maintenance tooling changes: also run `python3 -m unittest discover -s tests -v`.
- For imported code changes: also run `python -m pytest -c pytest.ini` in a Python environment
  with pytest, or use `../nadili/venv/bin/python`. These selected tests use disposable fixtures
  and fake review transports; do not run live providers or start the product.
- Before an authorized commit: run `python3 tools/snapshot.py check --staged` and
  `git diff --cached --check`. The hook validates the index but does not replace content review.
- Report changed files, checks and any unresolved failures. Do not declare completion with
  failed publication checks or silently bypass them.
