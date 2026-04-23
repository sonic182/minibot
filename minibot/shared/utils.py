from __future__ import annotations

from hashlib import sha1

from minibot.core.channels import ChannelMessage


def session_id_for(message: ChannelMessage) -> str:
    return session_id_from_parts(message.channel, message.chat_id, message.user_id)


def session_identifier(channel: str, chat_id: int | None, user_id: int | None) -> str:
    return f"{channel}:{chat_id or user_id or 0}"


def session_id_from_parts(channel: str, chat_id: int | None, user_id: int | None) -> str:
    identifier = session_identifier(channel, chat_id, user_id)
    return sha1(identifier.encode()).hexdigest()


def humanize_token_count(value: int) -> str:
    if abs(value) <= 9999:
        return str(value)
    short = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{short}k"


def summarize_items(items: list[str], *, preview_limit: int = 3) -> dict[str, object]:
    normalized = [item for item in items if item]
    preview = normalized[:preview_limit]
    suffix = ", ..." if len(normalized) > preview_limit else ""
    return {
        "count": len(normalized),
        "preview": ", ".join(preview) + suffix if preview else "none",
    }
