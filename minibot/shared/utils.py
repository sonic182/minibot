from __future__ import annotations

from hashlib import sha1
from typing import Any

from minibot.core.channels import ChannelMessage


def session_id_for(message: ChannelMessage) -> str:
    return session_id_from_parts(message.channel, message.chat_id)


def session_identifier(channel: str, chat_id: int | None) -> str:
    """Readable key for one chat session.

    MiniBot assists a single owner, so a session identifies a conversation, never a person. The
    sender's ``user_id`` is deliberately not part of the key: every chat belongs to the one owner
    configured in ``[runtime].owner_id``.
    """
    return f"{channel}:{chat_id or 0}"


def session_id_from_parts(channel: str, chat_id: int | None) -> str:
    identifier = session_identifier(channel, chat_id)
    return sha1(identifier.encode()).hexdigest()


def humanize_token_count(value: int) -> str:
    if abs(value) <= 9999:
        return str(value)
    short = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{short}k"


def validate_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_attachments, list):
        return []
    validated: list[dict[str, Any]] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        file_type = item.get("type")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(file_type, str) or not file_type.strip():
            continue
        attachment: dict[str, Any] = {"path": path.strip(), "type": file_type.strip()}
        caption = item.get("caption")
        if isinstance(caption, str) and caption.strip():
            attachment["caption"] = caption.strip()
        validated.append(attachment)
    return validated


def summarize_items(items: list[str], *, preview_limit: int = 3) -> dict[str, object]:
    normalized = [item for item in items if item]
    preview = normalized[:preview_limit]
    suffix = ", ..." if len(normalized) > preview_limit else ""
    return {
        "count": len(normalized),
        "preview": ", ".join(preview) + suffix if preview else "none",
    }
