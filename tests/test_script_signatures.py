from __future__ import annotations

from typing import Any

import pytest

from minibot.llm.tools.script_signatures import render_signature


@pytest.mark.parametrize(
    ("properties", "required", "expected"),
    [
        ({"path": {"type": "string"}}, ["path"], "await tools.t(*, path: str)"),
        ({"note": {"type": ["string", "null"]}}, ["note"], "await tools.t(*, note: str | None = None)"),
        ({"limit": {"type": "integer", "default": 5}}, [], "await tools.t(*, limit: int = ...)"),
        ({"mode": {"enum": ["a", "b"]}}, ["mode"], "await tools.t(*, mode: Literal['a', 'b'])"),
        (
            {"my-key": {"type": "string"}, "class": {"type": "integer"}, "q": {"type": "string"}},
            ["my-key", "q"],
            "await tools.t(*, q: str, **{'my-key': str, 'class': int = ...})",
        ),
    ],
    ids=["required", "nullable_required", "optional_default", "enum", "non_identifier_key"],
)
def test_render_signature(properties: dict[str, Any], required: list[str], expected: str) -> None:
    parameters = {"type": "object", "properties": properties, "required": required}

    assert render_signature("t", "", parameters) == expected
