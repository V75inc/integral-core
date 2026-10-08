"""Explicit encryption setup for tests that persist confidential receipts."""

import base64
import secrets

import pytest


@pytest.fixture(autouse=True)
def encrypted_model_storage(monkeypatch):
    """Use a temporary key without relying on a developer's .env."""
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    )
