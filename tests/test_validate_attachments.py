from __future__ import annotations

from minibot.shared.utils import validate_attachments


def test_validate_attachments_with_valid_single_attachment():
    raw = [
        {
            "path": "browser/screenshot.png",
            "type": "image/png",
            "caption": "Example screenshot",
        }
    ]
    result = validate_attachments(raw)
    assert len(result) == 1
    assert result[0]["path"] == "browser/screenshot.png"
    assert result[0]["type"] == "image/png"
    assert result[0]["caption"] == "Example screenshot"


def test_validate_attachments_with_multiple_valid():
    raw = [
        {"path": "browser/shot1.png", "type": "image/png", "caption": "First"},
        {"path": "browser/shot2.png", "type": "image/png"},
    ]
    result = validate_attachments(raw)
    assert len(result) == 2
    assert result[0]["caption"] == "First"
    assert "caption" not in result[1]


def test_validate_attachments_with_missing_path():
    raw = [
        {"type": "image/png", "caption": "Missing path"},
    ]
    result = validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_with_missing_type():
    raw = [
        {"path": "browser/shot.png", "caption": "Missing type"},
    ]
    result = validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_with_empty_strings():
    raw = [
        {"path": "", "type": "image/png"},
        {"path": "browser/shot.png", "type": ""},
        {"path": "  ", "type": "image/png"},
    ]
    result = validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_filters_invalid_items():
    raw = [
        {"path": "valid.png", "type": "image/png"},
        "not a dict",
        {"path": "missing-type.png"},
        None,
        {"path": "valid2.png", "type": "image/png"},
    ]
    result = validate_attachments(raw)
    assert len(result) == 2
    assert result[0]["path"] == "valid.png"
    assert result[1]["path"] == "valid2.png"


def test_validate_attachments_with_null_input():
    assert validate_attachments(None) == []


def test_validate_attachments_with_empty_array():
    assert validate_attachments([]) == []


def test_validate_attachments_with_non_list():
    assert validate_attachments("not a list") == []
    assert validate_attachments({"path": "test.png"}) == []


def test_validate_attachments_strips_whitespace():
    raw = [
        {"path": "  browser/shot.png  ", "type": "  image/png  ", "caption": "  Test  "},
    ]
    result = validate_attachments(raw)
    assert len(result) == 1
    assert result[0]["path"] == "browser/shot.png"
    assert result[0]["type"] == "image/png"
    assert result[0]["caption"] == "Test"
