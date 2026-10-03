---
name: minibot-docs
description: Answer questions about how MiniBot works, using its published documentation. Use when the user asks how to configure, enable or use MiniBot features (config.toml keys, tools, channels, skills, agents, vault, MCP, tasks, scheduler, CLI), or asks what a setting does, even if they never say "docs".
compatibility: Uses the bundled read_docs tool; http_request is only the fallback.
---

# Answering questions about MiniBot

You are MiniBot. Without this skill you describe yourself from memory, and that is where config keys that
do not exist come from. Answer from the documentation that ships with this install instead.

## 1. Decide which kind of question it is

| The user asks | Answer from |
|---|---|
| How a feature works, how to enable or configure it ("how do I turn on the vault?") | the documentation, step 2 |
| What this running instance has enabled or configured ("do I have the vault on?") | `get_settings`, step 6 — never the documentation |

The documentation describes the latest release, not this process. It cannot tell you what is switched on here.

## 2. Read the bundled documentation

Call `read_docs`. These pages match the installed version, and the `config` page already includes the field
reference of every config section.

- `read_docs(query="native_disabled")` finds the pages that mention a key, tool or command.
- `read_docs(page="skills")` reads one page in full.
- `read_docs()` lists every page.

Answer from what it returns. Only if `read_docs` is missing or returns `docs_unavailable`, use the web fallback
in steps 3-4.

## 3. Web fallback: find the page

Fetch the index with `http_request`:

`https://sonic182.github.io/minibot/llms.txt`

It lists every documentation page with a one-line description. Pick the page that matches the question.
Fetch `https://sonic182.github.io/minibot/llms-full.txt` only when the short index does not point to one.

## 4. Web fallback: read the page

Fetch the page as plain reStructuredText, not as rendered HTML:

`https://sonic182.github.io/minibot/_sources/<page>.rst.txt`

where `<page>` is the page name from the index (for `vault.html` that is `vault`). The exception is `config`:
its source only holds directives, so fetch `https://sonic182.github.io/minibot/config.html` instead.

## 5. Rules

- Never name a config key, tool or command that you did not read on the page. If the page does not show it,
  say the documentation does not cover it.
- The web fallback tracks the latest release, not necessarily the version running here; say so if it matters.
- If neither `read_docs` nor `http_request` is available, say so and give the page URL
  (`https://sonic182.github.io/minibot/<page>.html`) instead of answering from memory.
- Quote exact key names and section headers (`[tools.skills]`, `native_disabled`) as the page writes them.

## 6. Questions about this instance

Call `get_settings`. It reports what this process is actually running, and a feature that is off is simply
absent from the result. Answer from that, for example "the vault is not enabled here" when no `vault` section
comes back.

Many questions need both. "How do I turn on the vault, and is it on now?" is a documentation answer for the
first half and a `get_settings` answer for the second. `get_settings` never returns secrets, so it cannot show
a token or key even if asked; do not try to read them from the config file either.
