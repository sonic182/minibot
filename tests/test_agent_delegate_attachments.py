from __future__ import annotations

from minibot.llm.tools.agent_delegate import _extract_outcome, _validate_attachments


def test_validate_attachments_with_valid_single_attachment():
    raw = [
        {
            "path": "browser/screenshot.png",
            "type": "image/png",
            "caption": "Example screenshot",
        }
    ]
    result = _validate_attachments(raw)
    assert len(result) == 1
    assert result[0]["path"] == "browser/screenshot.png"
    assert result[0]["type"] == "image/png"
    assert result[0]["caption"] == "Example screenshot"


def test_validate_attachments_with_multiple_valid():
    raw = [
        {"path": "browser/shot1.png", "type": "image/png", "caption": "First"},
        {"path": "browser/shot2.png", "type": "image/png"},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 2
    assert result[0]["caption"] == "First"
    assert "caption" not in result[1]


def test_validate_attachments_with_missing_path():
    raw = [
        {"type": "image/png", "caption": "Missing path"},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_with_missing_type():
    raw = [
        {"path": "browser/shot.png", "caption": "Missing type"},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_with_empty_strings():
    raw = [
        {"path": "", "type": "image/png"},
        {"path": "browser/shot.png", "type": ""},
        {"path": "  ", "type": "image/png"},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 0


def test_validate_attachments_filters_invalid_items():
    raw = [
        {"path": "valid.png", "type": "image/png"},
        "not a dict",
        {"path": "missing-type.png"},
        None,
        {"path": "valid2.png", "type": "image/png"},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 2
    assert result[0]["path"] == "valid.png"
    assert result[1]["path"] == "valid2.png"


def test_validate_attachments_with_null_input():
    assert _validate_attachments(None) == []


def test_validate_attachments_with_empty_array():
    assert _validate_attachments([]) == []


def test_validate_attachments_with_non_list():
    assert _validate_attachments("not a list") == []
    assert _validate_attachments({"path": "test.png"}) == []


def test_validate_attachments_strips_whitespace():
    raw = [
        {"path": "  browser/shot.png  ", "type": "  image/png  ", "caption": "  Test  "},
    ]
    result = _validate_attachments(raw)
    assert len(result) == 1
    assert result[0]["path"] == "browser/shot.png"
    assert result[0]["type"] == "image/png"
    assert result[0]["caption"] == "Test"


def test_extract_outcome_with_plain_text_and_attachments():
    pre_response_meta = {
        "kind": "text",
        "attachments": [{"path": "browser/shot.png", "type": "image/png", "caption": "Test screenshot"}],
    }
    outcome = _extract_outcome("Screenshot taken", pre_response_meta)
    assert outcome.valid is True
    assert outcome.text == "Screenshot taken"
    assert len(outcome.attachments) == 1
    assert outcome.attachments[0]["path"] == "browser/shot.png"


def test_extract_outcome_with_multiple_attachments():
    pre_response_meta = {
        "attachments": [
            {"path": "file1.png", "type": "image/png"},
            {"path": "file2.pdf", "type": "application/pdf", "caption": "Report"},
        ],
    }
    outcome = _extract_outcome("Done", pre_response_meta)
    assert outcome.valid is True
    assert len(outcome.attachments) == 2
    assert outcome.attachments[0]["path"] == "file1.png"
    assert outcome.attachments[1]["caption"] == "Report"


def test_extract_outcome_without_attachments():
    outcome = _extract_outcome("Result", None)
    assert outcome.valid is True
    assert outcome.attachments == []


def test_extract_outcome_with_empty_text_is_invalid():
    outcome = _extract_outcome("", None)
    assert outcome.valid is False
    assert outcome.attachments == []


def test_extract_outcome_with_whitespace_only_is_invalid():
    outcome = _extract_outcome("   ", None)
    assert outcome.valid is False


def test_extract_outcome_string_payload():
    outcome = _extract_outcome("just a string", None)
    assert outcome.valid is True
    assert outcome.text == "just a string"
    assert outcome.attachments == []
