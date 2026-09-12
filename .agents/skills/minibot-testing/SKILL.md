---
name: minibot-testing
description: Write and review Minibot Python tests with concise pytest patterns. Use when adding or changing tests, test fixtures, shared factories, unittest.mock.patch, MagicMock, AsyncMock, monkeypatch, or test doubles in this repo.
---

# Minibot Testing

Start with the `minibot-dev` skill if you also need the architecture map, wiring chains, or
project invariants.

## Overview

Keep Minibot tests small, behavior-focused, and DRY. Prefer shared fixtures or small factories when the same fake/mock appears in more than one test.

## Workflow

- Inspect nearby tests first; follow their style unless it duplicates avoidable boilerplate.
- Prefer testing public behavior over private implementation details, except for small pure helpers where direct tests are cheaper and stable.
- Add the smallest test surface that proves the behavior or regression.
- Run targeted pytest for changed tests; run `ruff check` and `ruff format --check` for touched files.

## Test Doubles

- Use `pytest` fixtures in `tests/conftest.py` for reusable test doubles used by multiple files.
- Use local fixtures inside a test file when reuse is limited to that file.
- Use tiny factory functions for repeated model/config objects.
- Use `MagicMock` or `AsyncMock` when call assertions matter.
- Use `monkeypatch.setattr` for simple replacement of functions/constants.
- Use `unittest.mock.patch` when a context-managed patch is clearer or already used nearby.

## Preferences

- Do not duplicate fake classes across test files; extract a fixture or factory.
- Keep fixtures deterministic and explicit; avoid hidden global state.
- Prefer asserting outputs and important calls, not every internal call.
- For async code, use `pytest.mark.asyncio` and `AsyncMock` for awaited collaborators.
- Avoid broad integration tests when a focused unit test catches the behavior.
- Do not add tests for incidental implementation details just to increase coverage.

## Examples

Shared mock fixture:

```python
@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.search = AsyncMock(return_value=[])
    return client
```

Factory for repeated config objects:

```python
def _tool_config(**overrides: object) -> RagToolConfig:
    return RagToolConfig(**overrides)
```
