---
name: create_skill
description: Author a new MiniBot skill. Use when the user asks to create, add or save a skill, or when a repetitive multi-step workflow is worth capturing as reusable instructions.
---

# Creating a MiniBot skill

A skill is a directory containing a `SKILL.md` file: YAML-ish frontmatter plus Markdown
instructions. MiniBot discovers skills from disk and reloads them automatically, so a skill you
write is usable in the same conversation — no restart.

## 1. Find out where skills go

Call `list_skills` first. Its result carries everything you need:

- `write_dir` — the directory new skills belong in.
- `write_dir_access` — which tool can write there: `filesystem` or `bash`.
- `discovery_paths` — every directory skills are read from.
- each match's `source` — `project`, `user`, or `native` (bundled with MiniBot).

Do not guess these paths. They are configuration and they differ per install.

## 2. Check the name is free

A skill whose name already exists at a higher-precedence source will shadow yours, or be
shadowed by it. Precedence is `project` > `user` > `native`. Writing a skill that deliberately
overrides a bundled one is supported — just do it knowingly, and tell the user.

## 3. Write the files

```
<write_dir>/<skill-name>/SKILL.md
```

The directory name must equal the frontmatter `name`.

When `write_dir_access` is `filesystem`, write with the `filesystem` tool using a path relative
to its managed root. When it is `bash`, the directory sits outside that root and `filesystem`
will refuse it — use `bash` with a heredoc instead.

### Frontmatter

```
---
name: my-skill
description: One or two sentences saying WHEN to use this skill.
---
```

`enabled` is optional and defaults to true, so leave it out unless you are deliberately
switching a skill off.

MiniBot's parser is stricter than the agentskills.io spec. These are the rules that actually
apply here:

- Frontmatter is flat `key: value` only. It is **not** real YAML — indented lines are skipped,
  so nested structures are ignored rather than parsed.
- Only `name`, `description` and `enabled` are read. Other fields (`license`, `metadata`,
  `compatibility`, `allowed-tools`) are accepted and ignored, so they are safe to keep for
  portability but do nothing here.
- `description` should stay under 300 characters; longer descriptions log a warning.
- `enabled: false` hides a skill from the runtime. It is a MiniBot extension that other
  agent tools ignore. It applies to skills on disk; a `native` skill bundled with MiniBot is
  switched off through `[tools.skills] native_disabled` in `config.toml` instead, since nobody
  edits files inside an installed package.
- **An empty body silently drops the skill.** If a skill never shows up in `list_skills`, an
  empty or whitespace-only body after the frontmatter is the first thing to check.

### The description is the whole pitch

The description is the only part of a skill that reaches the system prompt. Everything else is
loaded only after `activate_skill` is called. So write the description to answer *when should I
reach for this*, not *what is this*:

- Good: "Review a pull request diff for behavioural bugs and regression risk. Use when asked to
  review a PR, a diff, or changes on a branch."
- Weak: "PR review helper."

### The body

Write instructions for an agent that has never seen the task. Concrete steps, exact commands,
real file paths, and the failure modes worth knowing. Keep `SKILL.md` under about 500 lines; put
long reference material in a `references/` subdirectory, one level deep, and point at it from
the body so it is read only when needed.

## 4. Verify

Call `list_skills` again. The new skill must appear, with `source: project` (or wherever you
wrote it). If it does not:

- the body is empty,
- the frontmatter is missing, malformed, or has no `name`,
- `enabled: false` is set,
- or the file is not at `<dir>/SKILL.md`.

Then call `activate_skill` with the exact name to confirm the instructions load as intended, and
report the created path back to the user.
