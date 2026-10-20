---
name: python-hex-review
description: Review and refactor MiniBot Python code to preserve its mini hex architecture. Use when checking layer boundaries and import direction across minibot.core/app/adapters/llm, async orchestration, or a refactor that may move logic into the wrong layer. Not for adding features (minibot-dev).
---

# Python Hex Review

Review and refactor MiniBot Python code without weakening its architecture. If you are *adding*
something rather than reviewing it, start with the `minibot-dev` skill.

Use this skill when the task involves:
- reviewing Python changes for architectural regressions
- moving logic between `core`, `app`, `adapters`, `llm`, `config`, `rag` or `shared`
- validating import direction and boundary ownership
- reviewing bundled or third-party extensions, their tools, event handlers, and services
- checking async correctness on I/O-heavy paths
- tightening a handler, service, adapter, or provider integration

## Priorities

1. Preserve MiniBot's current mini hex layout.
2. Preserve behavior unless the user requested a behavior change.
3. Prefer the smallest refactor that restores a boundary.
4. Keep I/O paths async-friendly.
5. Keep provider, transport, and storage details at the edge.

## Workflow

1. Identify the touched files and classify each one by layer.
2. Read [references/HEX_RULES.md](references/HEX_RULES.md) for layer ownership, import rules,
   extension boundaries and async rules.
3. Walk [references/REVIEW_CHECKLIST.md](references/REVIEW_CHECKLIST.md) over the change.
4. If proposing code movement, use [references/REFACTOR_PATTERNS.md](references/REFACTOR_PATTERNS.md)
   to keep the refactor incremental.
5. Check the actual imports rather than trusting the layer name: `rg -n "^\s*(from|import) minibot\." <files>`.
6. Report findings ordered by severity: architecture violation, correctness risk,
   maintainability issue, style issue.

If ownership is ambiguous, choose the most conservative interpretation that keeps dependencies
pointing inward.

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
