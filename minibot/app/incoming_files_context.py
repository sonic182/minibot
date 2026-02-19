from __future__ import annotations

from typing import Any, Sequence

from minibot.core.channels import ChannelMessage, IncomingFileRef


def incoming_files_from_metadata(metadata: dict[str, Any] | None) -> list[IncomingFileRef]:
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("incoming_files")
    if not isinstance(raw, list):
        return []
    parsed: list[IncomingFileRef] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(IncomingFileRef.model_validate(item))
        except Exception:
            continue
    return parsed


def summarize_attachments_for_memory(attachments: Sequence[dict[str, Any]]) -> str:
    summaries: list[str] = []
    for attachment in attachments:
        attachment_type = attachment.get("type")
        if attachment_type == "input_image":
            summaries.append("image")
            continue
        if attachment_type == "input_file":
            filename = attachment.get("filename")
            if isinstance(filename, str) and filename.strip():
                summaries.append(f"file:{filename.strip()}")
            else:
                summaries.append("file")
            continue
        summaries.append("attachment")
    return ", ".join(summaries)


def summarize_incoming_files_for_memory(incoming_files: Sequence[IncomingFileRef]) -> str:
    if not incoming_files:
        return ""
    return ", ".join([f"file:{item.filename}" for item in incoming_files])


def build_history_user_entry(message: ChannelMessage, model_text: str) -> str:
    base_text = message.text.strip() if message.text else ""
    attachment_summary = summarize_attachments_for_memory(message.attachments)
    incoming_files = incoming_files_from_metadata(message.metadata)
    incoming_file_summary = summarize_incoming_files_for_memory(incoming_files)
    if not attachment_summary and not incoming_file_summary:
        return base_text
    visible_text = base_text or model_text
    parts = [item for item in [attachment_summary, incoming_file_summary] if item]
    summary = ", ".join(parts)
    if visible_text:
        return f"{visible_text}\nAttachments: {summary}"
    return f"Attachments: {summary}"


def suggest_persist_destination(path: str) -> str | None:
    marker = "uploads/temp/"
    if path.startswith(marker):
        return f"uploads/{path[len(marker) :]}"
    return None


def build_incoming_files_text(prompt_text: str, incoming_files: Sequence[IncomingFileRef]) -> str:
    lines = [
        "Incoming managed files:",
        *[
            (
                f"- {item.filename} (path={item.path}, mime={item.mime}, size={item.size_bytes} bytes"
                f", source={item.source}, caption={item.caption or ''})"
            )
            for item in incoming_files
        ],
    ]
    first_path = incoming_files[0].path if incoming_files else ""
    suggested_destination = suggest_persist_destination(first_path)
    lines.append(
        "For file-management requests, use the filesystem tool "
        "(action=move, action=delete, action=send, action=list). "
        "Do NOT call self_insert_artifact unless user explicitly asks to inspect content."
    )
    if suggested_destination:
        lines.append(
            "If user asks to save the uploaded file, use filesystem action=move "
            f"source_path={first_path} destination_path={suggested_destination}."
        )
    lines.append("For analysis requests, use self_insert_artifact when inspection is required.")
    lines.append("If user intent is unclear, ask a clarifying question before acting.")
    if prompt_text:
        return f"{prompt_text}\n\n" + "\n".join(lines)
    return (
        "The user uploaded file(s) but did not include a clear instruction.\n"
        + "\n".join(lines)
        + "\nAsk the user what to do, unless the intent is already obvious."
    )
