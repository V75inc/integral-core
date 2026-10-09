"""Bootstrap never silently changes an existing encryption identity."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def run_bootstrap(dest, example):
    return subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/bootstrap_env.py"),
            str(dest),
            str(example),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def env_text(credential=None, oauth=None):
    credential = credential or base64.b64encode(b"a" * 32).decode()
    oauth = oauth or base64.urlsafe_b64encode(b"b" * 32).decode()
    return (
        f'JVSPATIAL_JWT_SECRET_KEY={"c" * 64}\n'
        f"INTEGRAL_CREDENTIAL_ENC_KEY={credential}\n"
        f"JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY={oauth}\n"
    )


@pytest.mark.parametrize("style", ["plain", "quoted", "comment", "spaces"])
def test_valid_keys_are_preserved_byte_for_byte_on_repeated_setup(tmp_path, style):
    body = env_text()
    if style == "quoted":
        body = (
            "\n".join(
                f'{key}="{value}"'
                for key, value in (line.split("=", 1) for line in body.splitlines())
            )
            + "\n"
        )
    elif style == "comment":
        body = body.replace("\n", " # retained key\n")
    elif style == "spaces":
        body = body.replace("=", " = ", 1)
    dest = tmp_path / ".env"
    dest.write_text(body)
    example = tmp_path / "example"
    example.write_text("")
    for _ in range(2):
        result = run_bootstrap(dest, example)
        assert result.returncode == 0, result.stderr
        assert dest.read_text() == body
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad_key", ["credential", "oauth", "duplicate"])
def test_bad_existing_key_refuses_without_partial_changes_or_secret_output(
    tmp_path, bad_key
):
    credential = "a-long-value-that-is-not-a-real-storage-encryption-key"
    body = env_text(
        credential=credential if bad_key == "credential" else None,
        oauth="broken-fernet-key" if bad_key == "oauth" else None,
    )
    if bad_key == "duplicate":
        body += f"INTEGRAL_CREDENTIAL_ENC_KEY={credential}\n"
    # A pending JWT generation must not be written before detecting the error.
    body = body.replace("c" * 64, "replace-with-jwt-key")
    dest = tmp_path / ".env"
    dest.write_text(body)
    example = tmp_path / "example"
    example.write_text("")
    result = run_bootstrap(dest, example)
    assert result.returncode != 0
    assert dest.read_text() == body
    assert "Existing keys were not changed" in result.stderr
    assert credential not in result.stderr
    assert "broken-fernet-key" not in result.stderr


def test_fresh_setup_generates_all_keys_once(tmp_path):
    dest = tmp_path / ".env"
    example = tmp_path / "example"
    example.write_text("DEBUG=true\n")
    result = run_bootstrap(dest, example)
    assert result.returncode == 0, result.stderr
    body = dest.read_text()
    assert all(
        key in body
        for key in (
            "JVSPATIAL_JWT_SECRET_KEY",
            "INTEGRAL_CREDENTIAL_ENC_KEY",
            "JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY",
        )
    )
    assert run_bootstrap(dest, example).returncode == 0
    assert dest.read_text() == body
