---
name: create-agent
description: Create, edit or delete a specialist agent at runtime. Use when the user asks to add an agent, specialist, sub-agent or helper with a specific job, to change how an existing one behaves, or to remove one.
---

# Creating a specialist agent

A specialist agent is a Markdown file: frontmatter plus a system prompt body. It gets its own model,
its own tool grants and its own instructions, and you delegate work to it with `spawn_task`.

Use the management tools, never a file write:

- `create_agent` — a new agent
- `update_agent` — replace an existing managed agent
- `delete_agent` — remove a managed agent
- `reload_agents` — only for files changed outside these tools

They validate the definition and the owner's ceiling **before** anything is written, and they refresh
the roster, so a new agent is delegatable in the same conversation. Writing the file yourself with
`filesystem` or `apply_patch` skips all of that: the roster will not see it until a reload, and the
reload only accepts it if it stays within the ceiling. A definition that exceeds the ceiling is
rejected, and one that is written under a file name other than its `name` cannot be edited or
deleted with these tools.

## 1. Decide what the agent is for

One clear job, in the user's words. "Reviews pull requests for security problems" is a specialist;
"helps with code" is a second copy of you, and not worth delegating to.

If the work is a repeatable procedure rather than a separate worker, a skill is the better fit —
see the `create-skill` skill. Delegation is for work that should run on its own, possibly in the
background, possibly on another model.

## 2. Check what you may grant

Call `get_settings` and read `agents.agent_management`:

- `tools_allow` — the only tool names the new agent may be granted. Anything else is rejected.
- `mcp_servers` — the only MCP servers it may claim.
- `providers` — the providers it may target. The main provider is always allowed.
- `directory` — where managed definitions live.

The ceiling is the owner's decision, not yours. If the agent needs a tool that is not in it, say so
and ask the owner to add it to `[orchestration.agent_management].tools_allow`; do not try to work
around it, and do not grant a broad tool to compensate for a missing narrow one.

## 3. Write the definition

    ---
    name: researcher
    description: Finds and summarises sources on a topic.
    mode: agent
    tools_allow:
      - http_request
      - filesystem
    ---

    You research a topic and return a short brief with sources. Prefer primary sources, and always
    include the URL you took each claim from.

Rules the validator enforces:

- `name` is 3 to 30 characters, letters and underscores only, and must match the `name` argument.
- `description` is what you and the user see in the roster. One line, in the third person.
- The body must not be empty. An empty body is a hard error, unlike a skill where it is dropped.
- `tools_allow` lists **exact** tool names. A pattern such as `file*` is rejected, and `tools_deny`
  is rejected outright, because it grants everything except what it lists.
- MCP access is claimed with `mcp_servers`, never as a tool name.
- `model_provider` must be a provider from the ceiling.
- `max_tool_iterations` and `timeout_seconds` are optional and capped by the task limits.

The body is the agent's whole system prompt. It does not inherit yours, so state what it must do,
what it must return, and what it must not do. Say how the result should be shaped — the worker's
answer is what comes back to you or to the chat.

## 4. Create it and confirm

Call `create_agent` with `name` and `definition`. The result reports `ok`, the roster, and on failure
an `error` naming exactly what to fix:

- `... is not allowed by [orchestration.agent_management].tools_allow` — the ceiling does not cover
  that tool.
- `... must use tools_allow, not tools_deny` — rewrite the grants.
- `... is a pattern; list exact tool names` — expand it.
- `frontmatter name 'x' must match the requested name 'y'` — they have to agree.
- `body prompt cannot be empty` — add the instructions.
- `... already exists; use update_agent` — or pick another name.

Fix the definition and call again. Nothing is written until it validates, so a rejected call leaves
the roster untouched.

Then tell the user the agent exists, what it can use, and that they can ask for work to be sent to
it. If creating it needed approval, they have already seen the prompt.

## Editing and deleting

`update_agent` replaces the whole file, so include everything that should remain — an omitted
`tools_allow` means the agent grants no native tools. `delete_agent` removes the definition; tasks
already running on it keep running.

Only agents in the managed directory can be changed or removed this way. An owner-authored agent is
not yours to edit, and `update_agent`/`delete_agent` will report that it does not exist.
