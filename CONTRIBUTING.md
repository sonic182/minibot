# Contributing to MiniBot

Thanks for your interest. MiniBot is MIT-licensed and contributions are welcome.

## Quick start

```bash
poetry install --all-extras   # always include extras, tests depend on them
poetry run python -m minibot.app.daemon
```

`AGENTS.md` has the full build/lint/test command list, the code style rules, and the
architecture summary. `ARCHITECTURE.md` is the long-form map. Read `AGENTS.md` before your
first change — it is short.

## Before you open a pull request

- **For non-trivial changes, open an issue or discussion first.** A design conversation before
  the code is written saves both of us a rewrite. Small fixes, typos and docs corrections do
  not need this.
- **Keep the change focused.** One concern per PR. Unrelated refactoring or reformatting mixed
  into a feature change is hard to review and will usually be asked to be split.
- **Say what you changed and why.** A PR body that explains the motivation gets reviewed
  faster than one that restates the diff.

## AI-assisted contributions

AI-assisted contributions are welcome — MiniBot is an AI agent project and is built with these
tools. But the tooling does not change who is responsible:

**You are solely responsible for what you submit.** You must understand every line, be able to
explain the "why" during review, and be willing to maintain it. "The AI wrote it" is not an
answer to a reviewer's question.

Two things that will get a PR closed regardless of who or what wrote it:

- **Shotgun refactoring** — wide-scale "improvements" produced by pointing a tool at the
  codebase, without prior discussion and without a concrete problem being solved.
- **Volume over intent** — several low-effort PRs fixing trivial or non-existent issues. One
  considered change is worth more than ten generated ones.

If AI was involved, you also warrant that the result does not include code regurgitated from
incompatibly licensed sources.

## What CI checks

Two workflows, and they check less than you might expect — so run the rest locally.

| Gate | Command |
|---|---|
| Custom name lint | `poetry run pylint --disable=all --enable=disallowed-name minibot` |
| Tests | `poetry run pytest` |
| Docs (on `main`, when docs or source change) | `poetry run sphinx-build -W -b html docs docs/_build/html` |

Two of these surprise people:

- **The name lint bans every 1–2 character identifier** except `i`, `j`, `x`, `y`, `mb`, `on`,
  `ok` (`pyproject.toml`). This is why the codebase writes `except … as exc` rather than
  `as e`. It runs on `minibot/` only.
- **The docs build uses `-W`**, so any new Sphinx warning fails it — including an RST heading
  without a blank line before it.

Ruff is **not** in CI, but it is the project style. Run it before pushing:

```bash
poetry run ruff check --fix minibot tests
poetry run ruff format .
```

## Style

The full rules are in `AGENTS.md`. The ones worth repeating here:

- Line length 119, Python 3.12–3.14, explicit type hints.
- Async-first on I/O paths; timezone-aware datetimes.
- No `print()` — Ruff `T201`; use structured logging.
- Avoid incidental comments. Docstrings are for public surfaces (tool descriptions, config
  models) or genuinely non-obvious constraints.
- **Never classify LLM intent or state by regex, substring, or ad-hoc text matching.** Use
  structured schema fields or model-native structured output. Text matching is for
  deterministic protocol parsing only (markdown fences, SSE framing).

Keep changes inside the existing layer boundaries — `core` (domain contracts), `app`
(orchestration), `adapters` (integrations), `llm` (provider and tool wiring). `ARCHITECTURE.md`
explains which is which.

## Tests

Add the smallest test surface that proves the behavior or the regression. Prefer integration
and functional tests over granular unit tests; broad, shallow coverage is not the goal.

```bash
poetry run pytest
poetry run pytest tests/test_foo.py -k test_name    # single test
```

`asyncio_mode` is `strict`: an async test without `@pytest.mark.asyncio` **silently does not
run**. The `e2e` suite is skipped unless `E2E_RUN` and the provider API keys are set — it calls
paid APIs, so leave it that way unless you mean it.

## Documentation

If you change behavior, update the docs in the same PR:

- `docs/*.rst` — user-facing reference, built with Sphinx
- `config.example.toml` — any new or changed configuration
- `ARCHITECTURE.md` — new modules or changed wiring
- `AGENTS.md` — only when a rule for working in the repo actually changes

`docs/llms.txt` and `docs/llms-full.txt` are not produced by the docs build — nothing
regenerates them automatically, so they need updating deliberately when you add or rename a
docs page. `tests/test_docs_llms_index.py` only checks that their links resolve, not that
pages are covered.

## Commits and PR hygiene

Commit subjects follow a loose conventional-commit style — `feat:`, `fix:`, `docs:`,
`refactor:`, `chore:`. Write the body to explain *why*, not *what*.

Rebase or merge `main` before asking for review if your branch has gone stale.

## Reporting bugs

Include the MiniBot version, Python version, the relevant `config.toml` section with secrets
removed, and what you expected versus what happened. Logs are helpful — **scrub tokens, API
keys and chat content before pasting them.**

For anything security-sensitive (a tool sandbox escape, a credential leak), please contact the
maintainer directly rather than opening a public issue. `docs/security.rst` covers the intended
sandboxing model.
