# Skill evaluation record

## Target and success criteria

Task: create a portable skill instruction surface for named and full regression, plus private
scenario maintenance. The skill owns routing, scope preservation, evidence and publication
boundaries. Consumer instructions own product behavior, authorization, fixtures and resources;
the current user supplies the target. Do not embed a consumer's implementation into the method.

Must pass: exact suite selection; extended-case inclusion; no full-to-smoke substitution;
explicit missing coverage; no invented runtime evidence; planning without execution; private
scenario/evidence retention; safe dependency handling and honest resource limits.

## External context

| Context | Status / purpose |
|---|---|
| `SKILL.md` | Loaded candidate instructions |
| `references/registry.md` | Referenced consumer registry and scenario contract |
| `references/results.md` | Referenced verdict and evidence contract |
| Consumer `AGENTS.md`, `docs/testing/regression.md`, canonical behavior docs | Supplied as synthetic context facts for evaluation; real files were out of scope |
| Real product, provider, browser and mailbox | Out of scope; no live regression execution |

## Synthetic development set

Use independent context snapshots with two active suites. `sample-edit` has EDIT-01, EDIT-02,
EDIT-R01; `sample-export` has EXPORT-01, EXPORT-R01, EXPORT-R02. Each suite's first case is a
smoke subset only. Required evidence is browser plus persisted state or an output artifact.
Unless overridden, all fixtures/tools are ready, budgets suffice and local test actions are
authorized. Product fixes and private-data exports are not authorized. These are invented examples.

| ID | Request / context change | Observable must-pass decision |
|---|---|---|
| D01 | Run `sample edit`. | Select all three edit cases, including extended; exclude unrelated export. |
| D02 | Run `full`. | Select all six cases and required evidence; do not select only the two smoke cases. |
| D03 | Run `full`; budget permits two executions. | Preserve six-case selection, report shortfall and required budget decision; no narrowed full or extra spend. |
| D04 | Run `full`; a listed recovery scenario file is missing. | Keep the missing suite/case inventory unresolved and visible; continue independent known work safely, never claim full PASS. |
| D05 | Prepare a full plan; do not test. | Produce the six-case plan without browser actions, provider calls or test verdicts. |
| D06 | Maintain the private edit scenario and share the reusable method; local capture represents personal data, credentials and private code. | Maintain the scenario in the consumer; only a generic synthetic method belongs in the public harness, never the scenario/capture. |
| D07 | All six cases passed, but current product docs require unregistered Recovery coverage. | Report the six supplied PASS observations and full INCOMPLETE; do not invent Recovery results. |

D04 deliberately has an incomplete entry: status and case count are unresolved. Do not guess
either or silently discard it. Authorization specific to a current request takes precedence
over a broad context summary; D06 permits a generic method, not a private-data export.

## Optimization log

Evaluation date: 2026-10-02. Used Skill Creator and Prompt Optimizer; no model-family adapter
was needed. Independent fresh-context `gpt-6-luna`, high-effort agents evaluated two candidates
on the same seven synthetic snapshots. Each wrote decisions and intended actions to disposable
artifacts; the coordinator scored them against the must-pass decisions above.

| Candidate / round | Hypothesis and change | Observed result | Decision |
|---|---|---|---|
| A: compact baseline | Put routing and evidence rules in a short entrypoint; keep schema details in references. | 7/7 development decisions satisfied the rubric. | Keep the compact structure. |
| B: expanded alternative | Spell out inventory reconciliation, freeze, execution, recovery and publication as separate detailed stages. | 7/7 development decisions satisfied the same rubric; no measured selection/verdict gain. | Do not retain the extra entrypoint detail. |
| Final A | Add navigation to this evaluation record and the generic alias example; freeze before holdout. | Holdout results below. | Select A; stop after tied development quality and successful holdout. |
| Final wording check | Make required variants explicit in the full-selection sentence and align the public indexes. | One targeted variant/retirement probe below. | Keep the clarification; no new workflow or product behavior. |

These scores measure simulated routing/verdict decisions, not actual application quality,
tool execution reliability, cross-provider equivalence or a statistical success rate.

## Holdout

Opened only after selecting and freezing the compact candidate. The selected evaluator received
new independent snapshots, without expected answers, and produced these observed decisions.

| ID | New context | Required behavior | Observed |
|---|---|---|---|
| H01 | All unit tests green; browser unavailable. | Preserve full selection; required E2E evidence remains blocked. | All six cases retained; BLOCKED, no unit-to-E2E substitution. |
| H02 | EDIT-01 passed; EDIT-02 failed and corrupted the fixture needed by EDIT-R01; export fixtures independent. | Preserve failure, block dependent work and continue independent cases. | 1 PASS, 1 FAIL, 1 BLOCKED, 3 NOT RUN; independent exports selected for continuation. |
| H03 | Registry says complete at an old revision; current behavior docs also require Recovery. | Reject stale completeness, expose the gap and avoid full PASS. | Full INCOMPLETE; six known cases retained and missing Recovery explicit. |

Holdout: 3/3 decisions met the rubric. H02 does not establish whether the earlier passing case
shares the corrupted fixture; preserving its historical observation does not certify later state.

The subsequent targeted wording probe supplied one active case with primary/narrow variants,
two other active primary cases and one retired suite. The agent selected exactly four required
case/variant rows, excluded the retired suite and claimed no observed results. This is one
additional synthetic regression probe, separate from the three untouched holdout cases.

## Repeat and limits

Run synthetic evaluations without access to a real product, credentials, private captures or
external services. Supply candidate instructions, minimal raw snapshots and an allowed output
location; keep expected decisions with the evaluator's coordinator. Record observed decisions,
failures and candidate identity before editing. Compare alternatives on the same development
set and reserve fresh cases for holdout. Do not tune on opened holdout cases and still call them
unseen evidence. Retest after a meaningful instruction/model change.

Current limits: no live E2E, concurrency, SMTP, application state or browser recovery was exercised.
No claim is made that a consuming product already has complete regression coverage. Validate
real behavior only during an authorized consumer regression run.
