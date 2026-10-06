"""Deployment configuration for the library's aggregate native token guard."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_native_token_budget_default():
    assert Settings.model_fields["INTEGRAL_NATIVE_TURN_TOKEN_LIMIT"].default == 300_000


def test_native_token_budget_reads_deployment_override(monkeypatch):
    monkeypatch.setenv("INTEGRAL_NATIVE_TURN_TOKEN_LIMIT", "450000")
    assert Settings(_env_file=None).INTEGRAL_NATIVE_TURN_TOKEN_LIMIT == 450_000


@pytest.mark.parametrize("value", ["0", "-1", "unbounded"])
def test_native_token_budget_rejects_invalid_override(monkeypatch, value):
    monkeypatch.setenv("INTEGRAL_NATIVE_TURN_TOKEN_LIMIT", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
