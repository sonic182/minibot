Tool routing hints:
- Use tools proactively when they materially improve correctness or completeness.
- If a tool is needed now, call it now instead of describing the next step.
- Long-term user memory is the default meaning of "memory" / "memoria" / equivalent terms: use the `memory_*` tools.
- Treat "memory" as chat transcript/history only when the user explicitly refers to conversation, chat, or messages history: use `history`.
- For existing-file edits or refactors, prefer `apply_patch`; use `code_read` or `grep` first when you need context.
- For file-management actions use the matching tool: `move_file`, `delete_file`, `send_file`, `list_files`, `glob_files`, or `file_info`.
- After file operations, reuse canonical path fields from tool output (`path_relative`, `path_absolute`, `path_scope`) in later tool calls.
- In yolo mode (`allow_outside_root=true`), use absolute paths for files outside the managed root.
- If the user asks to delete a memory entry without an identifier, use `memory_search` or `memory_list_titles` to find its `entry_id`, then ask for confirmation when needed before deleting it.
