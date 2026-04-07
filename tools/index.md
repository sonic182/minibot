# MiniBot Tools

This directory documents the current public MiniBot tool surface. Tool names here match the canonical names exposed by `minibot/llm/tools/factory.py`.

## Always Available

- [chat_history_info](chat_history_info.md): inspect the current chat-history message count.
- [chat_history_trim](chat_history_trim.md): remove old chat-history messages for the current conversation.

## Memory

- [memory](memory.md): save, retrieve, search, list, and delete user memory entries.

## Utility and Fetch

- [calculate_expression](calculate_expression.md): evaluate bounded arithmetic expressions with Decimal precision.
- [current_datetime](current_datetime.md): return the current UTC datetime.
- [http_request](http_request.md): fetch HTTP or HTTPS resources with response size controls and optional managed-file spillover.

## Host Execution and Editing

- [python_execute](python_execute.md): run host Python code with configurable timeout, sandbox mode, and artifact export.
- [python_environment_info](python_environment_info.md): inspect the Python runtime used by `python_execute`.
- [bash](bash.md): run host shell commands through `/bin/bash -lc`.
- [apply_patch](apply_patch.md): apply structured file patches.

## Managed Files

- [filesystem](filesystem.md): unified managed-file action facade for list, glob, info, write, move, delete, and send.
- [glob_files](glob_files.md): list files matching a glob under managed storage.
- [read_file](read_file.md): read a managed text file.
- [code_read](code_read.md): read a managed text file with line offset and limit.
- [grep](grep.md): search managed files with regex or fixed-string matching.
- [self_insert_artifact](self_insert_artifact.md): inject a managed file or image back into the runtime context.

## Audio

- [transcribe_audio](transcribe_audio.md): transcribe or translate managed audio files with `faster-whisper`.

## Scheduler

- [schedule](schedule.md): unified scheduled-prompt action facade for create, list, cancel, and delete.
- [schedule_prompt](schedule_prompt.md): create a scheduled prompt job.
- [list_scheduled_prompts](list_scheduled_prompts.md): list scheduled prompt jobs.
- [cancel_scheduled_prompt](cancel_scheduled_prompt.md): cancel a scheduled prompt job.
- [delete_scheduled_prompt](delete_scheduled_prompt.md): delete a scheduled prompt job.

## Delegation and Skills

- [fetch_agent_info](fetch_agent_info.md): inspect a specialist agent definition.
- [invoke_agent](invoke_agent.md): delegate a task to a specialist agent.
- [activate_skill](activate_skill.md): load full instructions for a discovered skill.

## Dynamic Tool Surfaces

- [mcp_dynamic_tools](mcp_dynamic_tools.md): dynamically discovered Model Context Protocol tools, exposed as `mcp_<server>__<remote_tool>`.

## Configuration Sources

Tool defaults are defined in `minibot/adapters/config/schema.py`. Example TOML configuration lives in `config.example.toml`; `config.yolo.toml` is a full-capability reference profile.

Hidden compatibility aliases are normalized at the execution boundary and are not primary public names. Current aliases include `http_client` to `http_request`, `calculator` to `calculate_expression`, `datetime_now` to `current_datetime`, and `artifact_insert` to `self_insert_artifact`.
