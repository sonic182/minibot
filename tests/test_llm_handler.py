from __future__ import annotations

from minibot.core.channels import ChannelMessage
from minibot.app.handlers.llm_handler import resolve_owner_id
from minibot.shared.utils import session_id_for


def _message(**overrides):
    base = {
        "channel": "telegram",
        "user_id": None,
        "chat_id": None,
        "message_id": None,
        "text": "hi",
        "metadata": {},
    }
    base.update(overrides)
    return ChannelMessage(**base)


def test_resolve_owner_prefers_default():
    message = _message(user_id=123)
    assert resolve_owner_id(message, "primary") == "primary"


def test_resolve_owner_uses_user_id():
    message = _message(user_id=42)
    assert resolve_owner_id(message, None) == "42"


def test_resolve_owner_uses_chat():
    message = _message(chat_id=987)
    assert resolve_owner_id(message, None) == "987"


def test_resolve_owner_falls_back_to_session_id():
    message = _message()
    expected = session_id_for(message)
    assert resolve_owner_id(message, None) == expected
