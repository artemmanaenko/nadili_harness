---
name: nadili-regression
description: Run named scenarios or full regression and maintain the consumer scenario registry.
---

# Regression

Work in the consuming product checkout, not this public harness. Read its AGENTS.md and
regression registry at docs/testing/regression.md. Resolve declared aliases; missing or
ambiguous targets need clarification rather than guessing. Register owner-supplied plans
privately using [the registry contract](references/registry.md).

A named suite selects every case and required variant including extended cases. Full selects
all active suites, cases and required variants; never replace it with smoke or an affected subset.
An explicit planning, skill-editing or scenario-maintenance request does not execute tests.

Read the selected protocols and freeze case IDs, build, fixture state and required evidence.
Honor existing permissions, resources and consumer gates; ask only for unresolved decisions.
Use one owner per browser and preserve dependencies between mutations. If budget is too small,
report the shortfall without reducing full. Run independent covered cases where safe.

Use real registered boundaries and observe both visible and persisted effects. A unit or mock
result cannot replace required E2E evidence. Negative asynchronous checks need processing state
and a bounded observation window. Do not automatically fix product defects, relax expectations,
retry uncertain writes or change configuration to bypass guards. Stop unsafe dependent work;
continue independent cases. Changed build/configuration/data invalidates affected prior evidence.

Keep every case and variant in [the result report](references/results.md) with PASS, FAIL,
BLOCKED or NOT RUN and evidence. Full PASS requires every active suite passing and a current,
complete product coverage inventory; missing coverage stays visible even when registered tests pass.
Preserve failed results, pending work and the denominator.

Maintain stable scenario IDs, fixture setup, oracles, dependencies and registry aliases in the
consumer. Record retirements; never change expectations just to match a defect. Keep product
scenarios, internal behavior, private code, identifiers, credentials, captures and configuration
out of this public harness; use synthetic examples only. Store reports privately and sanitize
versioned evidence. A skill update never starts the actual product.

For skill changes, use the synthetic [evaluation cases](references/evaluation.md); do not run
product scenarios while maintaining the skill. Keep invocations such as `send question` in
the consumer registry, so this method remains independent of product-specific behavior.
