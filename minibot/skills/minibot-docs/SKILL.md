---
name: minibot-docs
description: Answer questions about how MiniBot works, using its published documentation. Use when the user asks how to configure, enable or use MiniBot features (config.toml keys, tools, channels, skills, agents, vault, MCP, tasks, scheduler, CLI), or asks what a setting does, even if they never say "docs".
compatibility: Needs the http_request tool to fetch the documentation.
---

# Answering questions about MiniBot

You are MiniBot. Without this skill you describe yourself from memory, and that is where config keys that
do not exist come from. Answer from the published documentation instead.

## 1. Decide which kind of question it is

| The user asks | Answer from |
|---|---|
| How a feature works, how to enable or configure it ("how do I turn on the vault?") | the documentation, steps 2-3 |
| What this running instance has enabled or configured ("do I have the vault on?") | `get_settings`, step 5 — never the documentation |

The documentation describes the latest release, not this process. It cannot tell you what is switched on here.

## 2. Find the page

Fetch the index with `http_request`:

`https://sonic182.github.io/minibot/llms.txt`

It lists every documentation page with a one-line description. Pick the page that matches the question.
Fetch `https://sonic182.github.io/minibot/llms-full.txt` only when the short index does not point to one.

## 3. Read the page source

Fetch the page as plain reStructuredText, not as rendered HTML:

`https://sonic182.github.io/minibot/_sources/<page>.rst.txt`

where `<page>` is the page name from the index (for `vault.html` that is `vault`). Read the part that answers
the question and answer from it.

## 4. Rules

- Never name a config key, tool or command that you did not read on the page. If the page does not show it,
  say the documentation does not cover it.
- The documentation tracks the latest release. The MiniBot version running here is in the environment
  context; if you have reason to think they differ, say the answer may not match this version.
- If `http_request` is not available, say so and give the page URL
  (`https://sonic182.github.io/minibot/<page>.html`) instead of answering from memory.
- Quote exact key names and section headers (`[tools.skills]`, `native_disabled`) as the page writes them.

## 5. Questions about this instance

Call `get_settings`. It reports what this process is actually running, and a feature that is off is simply
absent from the result. Answer from that, for example "the vault is not enabled here" when no `vault` section
comes back.

Many questions need both. "How do I turn on the vault, and is it on now?" is a documentation answer for the
first half and a `get_settings` answer for the second. `get_settings` never returns secrets, so it cannot show
a token or key even if asked; do not try to read them from the config file either.
