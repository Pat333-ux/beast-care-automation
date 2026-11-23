from __future__ import annotations

import json

from src.observability import JsonRedactingFormatter, get_logger, sanitize_mapping


def test_sanitize_mapping_redacts_sensitive_fields_recursively() -> None:
    raw = {
        "case_id": "CASE-800",
        "applicant_name": "Jane Doe",
        "date_of_birth": "1989-02-14",
        "nested": {"notes": "Private case detail", "household_size": 3},
        "items": [{"ssn": "123-45-6789", "safe": True}],
    }
    cleaned = sanitize_mapping(raw)
    assert cleaned["case_id"] == "CASE-800"
    assert cleaned["applicant_name"] == "[REDACTED]"
    assert cleaned["date_of_birth"] == "[REDACTED]"
    assert cleaned["nested"]["notes"] == "[REDACTED]"
    assert cleaned["nested"]["household_size"] == 3
    assert cleaned["items"][0]["ssn"] == "[REDACTED]"


def test_json_formatter_outputs_valid_json_and_redacts_extra_context() -> None:
    logger = get_logger("beast_care_test")
    formatter = JsonRedactingFormatter()
    record = logger.makeRecord(
        name="beast_care_test", level=20, fn="test_observability.py", lno=1,
        msg="Case evaluated", args=(), exc_info=None,
        extra={"case_id": "CASE-801", "applicant_name": "Secret Person", "nested": {"token": "secret-token"}},
    )
    rendered = json.loads(formatter.format(record))
    assert rendered["message"] == "Case evaluated"
    assert rendered["context"]["case_id"] == "CASE-801"
    assert rendered["context"]["applicant_name"] == "[REDACTED]"
    assert rendered["context"]["nested"]["token"] == "[REDACTED]"
