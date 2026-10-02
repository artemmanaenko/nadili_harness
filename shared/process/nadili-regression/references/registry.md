# Consumer regression registry

Store the filled registry in the consuming repository, normally `docs/testing/regression.md`.
Follow that repository's document format. This reference is a generic contract; none of the
example names, paths, fixtures or case IDs below establish real product coverage.

The registry is the sole suite/alias index. Each scenario owns its detailed cases and oracles;
the registry must not duplicate case lists that can drift. Runtime secrets belong in approved
credential storage, never in this registry. Use labels for accounts and configured environments.

## Required information

- Owner, revision, canonical behavior sources and current product-area inventory.
- Coverage status: complete or incomplete, with the revision reviewed and explicit gaps.
- For each suite: stable ID, unique aliases, consumer-relative scenario path, active/retired
  status, product areas, dependencies/shared fixtures and required evidence layers.
- Whole-scenario selection, including extended cases and required variants. Optional smoke
  subsets may exist in a scenario, but neither a named-suite run nor `full` selects them by default.
- Supported environment/setup references, account labels, mutation permissions, provider/time
  budgets, wait/retry bounds, run-artifact destination and cleanup ownership. Reference maintained
  configuration rather than copying credentials, hostnames or ephemeral values.

## Minimal synthetic example

```markdown
# Regression registry

Owner: quality
Behavior source: docs/product.md
Coverage: incomplete
Coverage reviewed at: sample-revision
Private result location: docs/testing/results/
Environment contract: docs/testing/environment.md

| Suite | Aliases | Scenario | Status | Area | Dependencies | Evidence |
|---|---|---|---|---|---|---|
| sample-edit | sample edit, edit | docs/testing/sample-edit.md | active | Editing | isolated sample fixture | browser + persisted state |
| sample-export | sample export, export | docs/testing/sample-export.md | active | Export | sample-edit fixture, execute after edit | browser + output artifact |

## Coverage inventory

| Product area | Required suites | Status / gap |
|---|---|---|
| Editing | sample-edit | Covered by the current scenario |
| Export | sample-export | Covered by the current scenario |
| Recovery | none yet | Missing restart/recovery scenario; full cannot pass |
```

`sample edit` selects every case/variant in sample-edit. Its setup prerequisites may run, but
do not silently expand it to unrelated suites. `full` selects both active suites, including their
extended cases, and reports the recovery gap. It cannot call this a complete product regression.
Once an approved recovery scenario is registered, future `full` runs include it automatically.

## Scenario contract

A maintained scenario needs stable IDs for observable journeys and required variants, exact
starting state and test data labels, actions, expected visible/persisted effects, negative effects,
asynchronous completion criteria, dependencies and safe reset/cleanup. Identify the evidence layer
that proves each expectation and the required locale/viewport/environment matrix.

Separate a product's expected behavior from an unverified example question or test input. Before
running, bind each example to fixtures or a verified corpus snapshot. A missing fixture is a
prerequisite failure, not evidence that the product returned the expected negative result.

Preserve IDs through wording changes. Retire a case only with a recorded behavioral reason;
do not remove a failing case to make the suite pass. Keep dependent ordering explicit when one
case creates, publishes, removes or otherwise changes data consumed by another.

## Missing or stale registry

For an owner-requested setup/maintenance task, inspect the consumer's current test index and
named plans; create the index there, link existing scenarios and list uncovered areas. If the
named plan lives in another branch/worktree, record/select that checkout explicitly. Never copy
it into the public harness or hardcode a developer's machine path in the generic skill.

For an execution request, resolve the same facts before mutation. Missing files, conflicting
aliases, incomplete coverage or unknown revision stay visible. Independent registered work can
proceed within authorization; missing coverage prevents a full PASS. Do not invent a complete
inventory from the one feature mentioned most recently in chat.
