"""Tool schemas are checked once and then reused; error messages are unchanged."""

import jsonschema
import pytest

from app.services.hooks import tool_dispatch as td
from app.services.hooks.errors import ToolValidationFailedError

SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
}


def test_a_valid_payload_passes_and_an_invalid_one_gives_the_same_message_as_before():
    td.validate_input({"name": "x", "count": 2}, SCHEMA)
    with pytest.raises(ToolValidationFailedError) as cached:
        td.validate_input({"count": "two"}, SCHEMA)
    with pytest.raises(jsonschema.ValidationError) as plain:
        jsonschema.validate(instance={"count": "two"}, schema=SCHEMA)
    assert cached.value.message == f"tool input schema mismatch: {plain.value.message}"


def test_output_validation_uses_the_output_wording():
    with pytest.raises(ToolValidationFailedError, match="tool output schema mismatch"):
        td.validate_output({"count": 1}, SCHEMA)


def test_the_schema_itself_is_checked_once_not_on_every_call(monkeypatch):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    checks = []
    original = jsonschema.Draft202012Validator.check_schema.__func__

    def counting(cls, s, *a, **k):
        checks.append(1)
        return original(cls, s, *a, **k)

    monkeypatch.setattr(jsonschema.Draft202012Validator, "check_schema", classmethod(counting))
    for n in range(5):
        td.validate_input({"n": n}, schema)
    assert len(checks) == 1


def test_a_broken_schema_is_still_refused_and_the_cache_stays_bounded():
    with pytest.raises(jsonschema.SchemaError):
        td.validate_input({}, {"type": "not-a-type"})
    for i in range(td._MAX_VALIDATORS + 50):
        td.validate_input({"i": i}, {"type": "object", "title": str(i)})
    assert len(td._VALIDATORS) <= td._MAX_VALIDATORS
