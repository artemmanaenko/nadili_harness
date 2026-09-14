---
document_profile: human-primary
canonicality: canonical
owner: repository_owner
review_budget: 3 minutes
---

# Nadili harness

The project-owned delivery layer behind Nadili: agent roles, workflow adapters, independent
review, bounded retries and local quality gates. This repository shows the engineering of that
layer through its actual source and selected executable tests.

The working system combines Codex and Claude Code with externally installed gstack skills.
The stage contracts, orchestration, review runner and gate controls shown here belong to Nadili.
Third-party skill bodies are not included.

## Start here

| Question | Source |
|---|---|
| How does an idea reach integration? | [Delivery stages](shared/process/SKILL.md) and [gstack adapter](adapters/gstack/adapter.md) |
| Who may implement, review or test? | [Orchestrator](adapters/gstack/orchestrator.md) and [agent roles](adapters/gstack/roles/) |
| How are review results bound to the candidate? | [Review runner](shared/scripts/codex_code_review.py) and [correction packets](shared/scripts/codex_review_packet.py) |
| What stops repeated attempts from running indefinitely? | [Durable action ledger](shared/scripts/codex_orchestration_budget.py) |
| How do concurrent checks share one machine? | [Execution leases](shared/scripts/gate_executor.py) and [gate routing](shared/scripts/gate_lanes.py) |
| What happens after crashes, stale state or candidate changes? | [Selected behavioral tests](shared/tests/unit/) |

The workflow separates implementation, independent review and applicable runtime QA. Review
evidence follows a specific candidate. Expensive retries have explicit limits and recovery rules.
The ledger limits recorded actions; it does not intercept every native agent call or enforce a
total token-billing quota.

## Publication decisions

**D-001 — One source of truth.** Development stays in the private Nadili repository. This repository
receives a one-way source snapshot. Local edits inside the exported trees are protected from being
silently overwritten; make lasting harness changes in Nadili and sync them after committing there.

**D-002 — Current files only.** [The explicit manifest](export-manifest.json) names every imported
file and its destination. Sync reads blobs from one resolved source commit, preserving their executable modes.
Uncommitted source changes, history, ignored files and newly added paths are never copied
automatically. [The lock](snapshot.lock.json) records the revision and exported hashes.

**D-003 — Keep the showcase focused.** Production release instructions, product runtime code,
application prompts, real evaluation data and session outputs are outside the export. Explicit
documentation substitutions remove private pipeline details and internal evaluation module names.
Their exact replacements are reviewable in the manifest; source drift stops sync.
Legacy ledger fixtures are small synthetic scenarios maintained in this portfolio, not exported
from real work items. Test filename substitutions are pinned with expected occurrence counts.

## Repository layout

| Directory | Responsibility |
|---|---|
| [adapters/TRIP](adapters/TRIP/) | Legacy TRIP planning/implementation and shims. |
| [adapters/gstack](adapters/gstack/) | Current adapter, proportional workflow, review cycle and Codex agent roles. |
| [shared](shared/) | Shared stage contracts, entrypoints, execution tools, gates and tests. |

[File guide](FILE_GUIDE.md) explains every file in one line.
The manifest maps original Nadili paths to this layout. Sync rewrites references to exported
files in Markdown and agent TOML, including document-relative links. These copies are derived
exhibits; the original Nadili checkout keeps its own layout and canonical contracts.

Executable source remains in its original internal layout inside `shared/` so the selected
behavioral tests still run. Those tools include the review runner and action ledger used by
the current gstack route; adapter directories own workflow policy and role configuration.
The integration procedure and cleanup are shared because the gstack route reuses those steps.
The TRIP shim checker and its two pre-commit invocations are excluded from this export.
Legacy instructions may still reference the checker available in private Nadili.

Some references target private Nadili documents, installed external skills or runtime commands
that are intentionally absent. They retain their original meaning and paths. The legacy
`.claude/skills/codex-*` CLI wrappers are excluded; they remain available in private Nadili.
The portfolio does not deploy or operate the application.
The optional `worktree-setup.sh` recovery/bootstrap helper remains in private Nadili;
references to it in source exhibits do not imply that it is included here.

## Local maintenance

With this checkout next to `../nadili`:

```sh
python3 tools/snapshot.py sync
python3 tools/snapshot.py check
python3 tools/snapshot.py install-hook
```

Use `sync --source /path/to/nadili` for another source checkout. These commands do not fetch,
stage, commit, push or change the source repository. Sync changes only the managed exported files,
its lock and the generated ignore rules. README and maintenance tooling stay local to this repo.

The generated `.gitignore` exposes only approved files. The local pre-commit hook checks the
actual staged file set and content, including force-added files. It also checks snapshot hashes,
file types and common credential patterns. It does not replace review of newly selected content.
Hooks are local Git state: rerun `install-hook` after cloning; it preserves unrelated hooks.

## Checks

Python 3.12+ and Git are required. The publication guard tests use only the standard library:

```sh
python3 -m unittest discover -s tests -v
```

The selected original harness tests require pytest. With the existing adjacent Nadili environment:

```sh
../nadili/venv/bin/python -m pytest -c pytest.ini
```

Those tests exercise disposable local fixtures, including fake review transports. They do not
call an AI provider or start the Nadili application. Running the real review workflow still needs
its CLI, external skills and the owning project's private contracts.

No source history or runtime logs belong in this repository. Keep real review outputs,
credentials, nested worktrees and operator configuration outside the public file set.
