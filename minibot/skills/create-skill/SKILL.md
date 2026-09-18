---
name: create-skill
description: Author or update a MiniBot skill. Use when the user asks to create, add, save or edit a skill, or asks to remember a workflow, automate it for next time, or capture what you just did as reusable instructions — even if they never say the word "skill".
---

# Creating a MiniBot skill

A skill is a directory containing a `SKILL.md` file: frontmatter plus Markdown instructions.
MiniBot rereads skills from disk automatically, so one you write is usable in the same
conversation — no restart.

## 1. Ground it in what actually happened

The most common way to produce a worthless skill is to write it from general knowledge. That
yields vague procedure — "handle errors appropriately", "follow best practices" — instead of the
specific commands, conventions and edge cases that make a skill worth loading.

Write it from the conversation you just had:

- the steps that actually worked, in the order they worked
- the corrections the user made ("use X, not Y", "check Z first")
- exact commands, paths, and input/output formats
- project facts you had to be told and would not have guessed

If the user asks for a skill cold, with no such history, ask them for a concrete example, a
runbook, or a past task to work from before writing anything.

## 2. Find out where skills go

Call `list_skills` first. Its result carries everything you need:

- `write_dir` — the absolute path of the directory new skills belong in.
- `write_dir_access` — which tool can actually write there: `filesystem`, `bash`, or
  `unavailable`.
- `write_dir_filesystem_path` — when access is `filesystem`, the path to pass to that tool,
  already in the form it expects. Use it verbatim.
- `discovery_paths` — every directory skills are read from.
- each match's `source` — `project`, `user`, or `native` (bundled with MiniBot).

Do not guess these paths, and do not assume a writer is available. They are configuration and
they differ per install.

If `write_dir_access` is `unavailable`, stop and tell the user: neither `[tools.file_storage]`
nor `[tools.bash]` can reach `write_dir`. They can enable `[tools.bash]`, or point
`[tools.skills] write_path` at a directory inside the `[tools.file_storage] root_dir`. Do not
try to write anyway.

## 3. Pick a name

Lowercase, hyphen-separated, matching the directory: `pr-review`, not `PR Review`. MiniBot
accepts looser names, but other agent tools reading the same directory do not.

A name that already exists at a higher-precedence source shadows yours, or is shadowed by it.
Precedence is `project` > `user` > `native`. Overriding a bundled skill by reusing its name is
supported — just do it knowingly and say so.

## 4. MiniBot gotchas

Read these before writing, not after something silently fails:

- **An empty body drops the skill silently.** No error, no log line the user will see — it just
  never appears in `list_skills`.
- **Frontmatter is flat `key: value`, not real YAML.** Nested keys and list items are skipped;
  only block scalars (`description: |` or `>`) are read across lines. Keep values on one line anyway.
- **Only `name`, `description` and `compatibility` are read.** `license`, `metadata` and
  `allowed-tools` are accepted and ignored — safe to keep for portability, but inert here.
- `description` warns above 300 characters (the spec's hard limit is 1024).
- If the skill needs a particular tool or program (`bash`, `git`, an HTTP tool), say so on one
  line in `compatibility`: `activate_skill` returns it, so a bot without that tool can tell the
  user instead of improvising.
- There is no `enabled` switch. A skill is on while its directory exists; to turn one off, remove
  or move the directory. A bundled `native` skill is switched off with `[tools.skills]
  native_disabled` in `config.toml`.

## 5. Write the files

```
<write_dir>/<skill-name>/SKILL.md
```

When `write_dir_access` is `filesystem`, write with the `filesystem` tool, building the path
from `write_dir_filesystem_path` rather than from `write_dir` — the tool is confined to its
managed root and takes a root-relative path there. When access is `bash`, the directory is out
of the `filesystem` tool's reach; use `bash` with a heredoc and the absolute `write_dir`.

### Skeleton

```markdown
---
name: my-skill
description: One or two sentences saying WHEN to use this skill.
---

# What this skill does

One line of context.

## Steps

1. Concrete action, with the exact command
2. Next action
3. How to check it worked

## Gotchas

- Environment-specific fact that defies a reasonable assumption.
```

### The description is the whole pitch

The description is the only part of a skill that reaches the system prompt; the body loads only
after `activate_skill`. So it has to carry the entire triggering decision.

- Say **when to reach for it**, not what it is. "PR review helper" is useless; "Review a pull
  request diff for behavioural bugs and regression risk. Use when asked to review a PR, a diff,
  or changes on a branch" triggers.
- Be pushy. List the situations it applies to, including ones where the user never names the
  domain: "…even if they don't mention 'CSV'".
- Describe user intent, not internals.

### The body

- **Add what the agent lacks; cut what it already knows.** For each line ask "would the agent
  get this wrong without it?" If no, delete it. No explaining what JSON or git is.
- **One default, not a menu.** "Use pdfplumber; for scanned PDFs use pdf2image instead" beats
  listing four libraries as equals.
- **Teach the approach, not the answer.** A method that generalizes to the next instance of the
  problem, not the solution to this one instance.
- **Match prescriptiveness to fragility.** Destructive or order-sensitive steps get exact
  commands and "do not add flags". Where several approaches work, say *why* rather than
  dictating — an agent that knows the reason decides better in cases you did not foresee.
- **Gotchas earn their space.** Concrete corrections to mistakes the agent would otherwise make
  are usually the most valuable part of a skill.

Keep `SKILL.md` under about 500 lines. Longer reference material goes in `references/`, one
level deep — and **say when to load each file**: "Read `references/schema.md` before writing a
query" works, "see `references/` for details" does not, because the agent can only defer loading
if it knows the trigger.

## 6. Verify, then test the trigger

Call `list_skills` again. The new skill must appear, with `source: project` (or wherever you
wrote it). If it does not, check in this order: empty body, missing or malformed frontmatter, no
`name`, file not at `<dir>/SKILL.md`.

Then call `activate_skill` with the exact name to confirm the instructions load as intended.

Finally, test that it would actually trigger: call `list_skills` with a query phrased the way
the user would really ask, not using the skill's own name. Matching runs exact name → prefix →
substring → description substring → fuzzy, so if a realistic phrasing does not surface the
skill, the description is too narrow. Widen the situations it names rather than pasting in that
one phrase — fitting the description to a single query is how it ends up failing the next one.

Report the created path back to the user.
