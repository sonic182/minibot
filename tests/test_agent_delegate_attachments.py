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


def test_extract_outcome_with_valid_single_attachment():
    payload = {
        "answer": {"kind": "text", "content": "Screenshot taken"},
        "should_answer_to_user": True,
        "attachments": [{"path": "browser/shot.png", "type": "image/png", "caption": "Test screenshot"}],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert outcome.text == "Screenshot taken"
    assert outcome.should_answer_to_user is True
    assert len(outcome.attachments) == 1
    assert outcome.attachments[0]["path"] == "browser/shot.png"


def test_extract_outcome_with_multiple_attachments():
    payload = {
        "answer": {"kind": "text", "content": "Done"},
        "should_answer_to_user": True,
        "attachments": [
            {"path": "file1.png", "type": "image/png"},
            {"path": "file2.pdf", "type": "application/pdf", "caption": "Report"},
        ],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert len(outcome.attachments) == 2
    assert outcome.attachments[0]["path"] == "file1.png"
    assert outcome.attachments[1]["caption"] == "Report"


def test_extract_outcome_without_attachments_field():
    payload = {
        "answer": {"kind": "text", "content": "Result"},
        "should_answer_to_user": True,
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert outcome.attachments == []


def test_extract_outcome_with_null_attachments():
    payload = {
        "answer": {"kind": "text", "content": "Result"},
        "should_answer_to_user": True,
        "attachments": None,
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert outcome.attachments == []


def test_extract_outcome_with_empty_attachments_array():
    payload = {
        "answer": {"kind": "text", "content": "Result"},
        "should_answer_to_user": True,
        "attachments": [],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert outcome.attachments == []


def test_extract_outcome_with_invalid_attachments():
    payload = {
        "answer": {"kind": "text", "content": "Result"},
        "should_answer_to_user": True,
        "attachments": [
            {"path": "valid.png", "type": "image/png"},
            {"path": "missing-type.png"},
            "not a dict",
        ],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is True
    assert len(outcome.attachments) == 1
    assert outcome.attachments[0]["path"] == "valid.png"


def test_extract_outcome_invalid_payload_has_empty_attachments():
    payload = {
        "answer": {"kind": "text", "content": ""},
        "should_answer_to_user": True,
        "attachments": [{"path": "test.png", "type": "image/png"}],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is False
    assert len(outcome.attachments) == 1


def test_extract_outcome_string_payload_has_empty_attachments():
    payload = "just a string"
    outcome = _extract_outcome(payload)
    assert outcome.valid is False
    assert outcome.attachments == []


def test_extract_outcome_missing_should_answer_preserves_attachments():
    payload = {
        "answer": {"kind": "text", "content": "Result"},
        "attachments": [{"path": "test.png", "type": "image/png"}],
    }
    outcome = _extract_outcome(payload)
    assert outcome.valid is False
    assert outcome.error_code == "invalid_payload_schema"
    assert len(outcome.attachments) == 1
