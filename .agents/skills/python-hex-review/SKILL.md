---
name: python-hex-review
description: Review and refactor Python code to preserve Minibot's mini hex architecture. Use when checking layer boundaries across `minibot.core`, `minibot.app`, `minibot.adapters`, `minibot.llm`, channel handlers, provider/tool integrations, async orchestration, dependency direction, or when a refactor risks moving logic into the wrong layer.
---

# Python Hex Review

Review and refactor Minibot Python code without weakening its architecture.

Use this skill when the task involves:
- reviewing Python changes for architectural regressions
- moving logic between `core`, `app`, `adapters`, `llm`, or `shared`
- validating import direction and boundary ownership
- checking async correctness on I/O-heavy paths
- tightening a handler, service, adapter, or provider integration

## Priorities

1. Preserve Minibot's current mini hex layout.
2. Preserve behavior unless the user requested a behavior change.
3. Prefer the smallest refactor that restores a boundary.
4. Keep I/O paths async-friendly.
5. Keep provider, transport, and storage details at the edge.

## Workflow

1. Identify the touched files and classify each one by layer.
2. Read [references/HEX_RULES.md](references/HEX_RULES.md) for layer ownership and import rules.
3. Read [references/REVIEW_CHECKLIST.md](references/REVIEW_CHECKLIST.md) while reviewing behavior, async flow, and dependency direction.
4. If proposing code movement, use [references/REFACTOR_PATTERNS.md](references/REFACTOR_PATTERNS.md) to keep the refactor incremental.
5. Report findings ordered by severity: architecture violation, correctness risk, maintainability issue, style issue.

## Review output

When reviewing, return:

### Layer classification
- file or module
- suspected layer
- short reason

### Findings
- severity
- issue
- violated boundary or rule
- minimal recommended change

### Refactor plan
- minimal safe changes
- optional follow-up cleanup

Only include refactored code when the user asked for code changes or an example.

## Minibot-specific expectations

- `minibot.core` stays free of framework, transport, database, provider, and filesystem details.
- `minibot.app` orchestrates runtime flow and policy, but should not absorb adapter or provider specifics.
- `minibot.adapters` owns concrete integrations for config, messaging, memory, files, scheduler, logging, and MCP clients.
- `minibot.llm` owns provider wiring, request shaping, schema policy, and tool integration details.
- Channel handlers stay thin and should not become a second application layer.
- `minibot.shared` should stay small and generic; do not turn it into a dumping ground for mixed-layer logic.

## Async rules

- Prefer `async def` across I/O paths.
- Do not hide blocking work in handlers, tool execution, or provider calls.
- Keep cancellation, retry, and timeout policy in orchestration or dedicated helpers.
- Keep side effects explicit and close to the boundary that owns them.

## Ambiguity handling

If the current ownership is ambiguous, choose the most conservative interpretation that keeps dependencies pointing inward.
