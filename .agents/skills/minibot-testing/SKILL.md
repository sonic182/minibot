---
name: minibot-testing
description: Write and review MiniBot Python tests with concise pytest patterns. Use when adding or changing tests, fixtures, shared fakes or factories, MagicMock/AsyncMock/patch/monkeypatch doubles in this repo. Not for production code changes (minibot-dev) or architecture review (python-hex-review).
---

# MiniBot Testing

Keep tests small, behavior-focused and DRY. Use the `minibot-dev` skill for the wiring a test
exercises.

## Workflow

1. Find the nearest existing test file (`tests/test_<area>.py`, flat layout) and follow its
   style unless it duplicates avoidable boilerplate.
2. Pick the level. Prefer a functional test through the real wiring when the behavior crosses
   components (dispatcher, extensions, tool assembly, workers); use a focused unit test for a
   pure helper. Add the fewest tests that prove the behavior or regression.
3. Reuse shared doubles before writing new ones (below).
4. Run only the files you touched (`AGENTS.md`: `poetry run pytest <file>`), then
   `poetry run ruff check` and `poetry run ruff format --check` on them.

## Gotchas

- **`asyncio_mode = strict`** (`pytest.ini`): an async test without `@pytest.mark.asyncio`
  **silently does not run**. Mark every async test.
- **`python_files = tests/test_*.py`** is path-shaped: a test file outside `tests/` or not named
  `test_*.py` is never collected.
- **Markers** are declared in `pytest.ini`: `e2e`, `timeout` (pytest-timeout, e.g.
  `@pytest.mark.timeout(10)`), `allow_catalog_fetch`.
- **The models.dev catalog is stubbed for every test** by the autouse fixture
  `isolated_model_limits_cache` (`tests/conftest.py`). A test that drives the catalog fetch
  itself marks `allow_catalog_fetch` and stubs the transport.
- **e2e tests call paid APIs.** `tests/test_e2e_*.py` stay green in CI only through a
  module-level skip guard requiring `E2E_RUN`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY`. Never
  remove it.
- **CI runs jscpd over `tests/`** (`.jscpd.json`, `threshold: 3`): copy-pasted setup blocks fail
  CI. Extract a fixture or helper instead.
- **`AppContainer` is class-level state.** Reset it between configurations
  (`reset_container` in `tests/fixtures/console_harness.py`).
- **Direct `Settings(...)` / `Settings.model_validate(...)` skip `${ENV}` expansion**; go through
  `Settings.from_dict(...)` to test interpolation.
- Coverage is collected but never gated (`--cov-fail-under=0`); do not add tests for coverage.
- The pylint name rule runs on `minibot/` only, so short names are legal in tests.

## Shared doubles

Use these before writing a new fake:

| Need | Use |
|---|---|
| Scripted LLM replies and tool calls | `ScriptedLLMClient` / `ScriptedLLMFactory` (`tests/fixtures/llm/mock_client.py`) |
| Raw provider message/tool-call shapes | `FakeMessage` / `FakeToolCall` (`tests/fixtures/llm/fakes.py`) |
| In-memory conversation memory | `InMemoryMemoryStore` (`tests/fixtures/memory.py`) |
| A full console turn through the real container and dispatcher | `write_config`, `write_agent`, `run_console_turn` (`tests/fixtures/console_harness.py`); see `tests/test_console_functional.py` |
| A real MCP server | stdio/HTTP servers under `tests/fixtures/mcp/` |
| An extension module | write it to `tmp_path`, `monkeypatch.syspath_prepend`, pop it from `sys.modules` (`_write_module` in `tests/test_extensions.py`) |

Placement: a double used by several files goes in `tests/fixtures/` (helpers) or
`tests/conftest.py` (fixtures); one used by a single file stays a local fixture or a tiny
`_factory(**overrides)` function in that file.

## Doubles

- `AsyncMock` for awaited collaborators, `MagicMock` when call assertions matter.
- `monkeypatch.setattr` for simple replacement; `unittest.mock.patch` when a context-managed
  patch is clearer or already used nearby (the console harness patches `AppContainer` getters).
- Assert outputs and important calls, not every internal call. Keep fixtures deterministic, with
  no hidden global state.

```python
@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.search = AsyncMock(return_value=[])
    return client
```
