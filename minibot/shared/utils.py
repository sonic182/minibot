from __future__ import annotations

from hashlib import sha1

from minibot.core.channels import ChannelMessage


def session_id_for(message: ChannelMessage) -> str:
    identifier = f"{message.channel}:{message.chat_id or message.user_id or 0}"
    return sha1(identifier.encode()).hexdigest()
