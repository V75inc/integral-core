"""F0 install-onboarding gates — README, sandbox boot, Core-only verify.

These catch the three public-clone failures:

1. Quick start omitted ``.env`` / JWT secret, so ``python -m app.main`` died
   with ``SECRET_KEY must be at least 32 characters``.
2. ``sandbox-up.sh`` printed the API docs URL even when ``:4002`` refused
   connections.
3. ``make verify-core-only`` printed ``rg: command not found`` and still
   reported OK.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core_only, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[3]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        **kwargs,
    )


def test_readme_quick_start_has_docker_pypi_and_env() -> None:
    """Root README must document Docker, PyPI, and the JWT env var."""
    readme = (REPO / "README.md").read_text()
    assert "docker compose" in readme.lower()
    assert "pip install" in readme
    assert "JVSPATIAL_JWT_SECRET_KEY" in readme
    assert ".env" in readme
    assert "integral-core" in readme


def test_boot_guard_names_canonical_jwt_env_var() -> None:
    """Boot fatal must name JVSPATIAL_JWT_SECRET_KEY, not a bare SECRET_KEY."""
    src = (REPO / "backend" / "app" / "main.py").read_text()
    fatal = src.split("FATAL: SECRET_KEY", 1)[1].split("sys.exit(1)", 1)[0]
    assert "JVSPATIAL_JWT_SECRET_KEY" in fatal
    assert "SECRET_KEY in your environment" not in fatal


def test_sandbox_up_fails_closed_when_api_unreachable() -> None:
    """sandbox-up.sh must wait for health before printing the docs URL."""
    src = (REPO / "scripts" / "sandbox-up.sh").read_text()
    assert "wait_for_http" in src
    assert "exit 1" in src
    # Must not print the docs URL unless health succeeded.
    health_then_success = src.split("Sandbox up:", 1)
    assert len(health_then_success) == 2
    before_success = health_then_success[0]
    assert "wait_for_http" in before_success


def test_wait_for_http_fails_on_connection_refused() -> None:
    """wait_for_http.sh exits non-zero when the URL never answers."""
    script = REPO / "scripts" / "wait_for_http.sh"
    assert script.is_file()
    result = _run(
        ["bash", str(script), "http://127.0.0.1:1/health", "2", "0.05"],
        timeout=10,
    )
    assert result.returncode != 0


def test_bootstrap_env_replaces_placeholders(tmp_path: Path) -> None:
    """bootstrap_env.sh copies the example and replaces placeholder secrets."""
    script = REPO / "scripts" / "bootstrap_env.sh"
    assert script.is_file()
    example = tmp_path / "example.env"
    example.write_text(
        "JVSPATIAL_JWT_SECRET_KEY=replace-with-openssl-rand-hex-32-chars-min\n"
        "INTEGRAL_CREDENTIAL_ENC_KEY=replace-with-openssl-rand-base64-32\n"
        "OTHER=keep-me\n",
        encoding="utf-8",
    )
    dest = tmp_path / ".env"
    result = _run(["bash", str(script), str(dest), str(example)], timeout=10)
    assert result.returncode == 0, result.stderr
    text = dest.read_text(encoding="utf-8")
    assert "replace-with" not in text
    assert "OTHER=keep-me" in text
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    jwt = values["JVSPATIAL_JWT_SECRET_KEY"]
    cred = values["INTEGRAL_CREDENTIAL_ENC_KEY"]
    assert len(jwt) >= 32
    padding = "=" * (-len(cred) % 4)
    decoded = base64.b64decode(cred + padding)
    assert len(decoded) == 32


def test_core_no_app_import_check_ok_without_rg(tmp_path: Path) -> None:
    """core_no_app_import_check.sh succeeds via grep when rg is absent."""
    gate = REPO / ".ci" / "core_no_app_import_check.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (
        "bash",
        "sh",
        "grep",
        "echo",
        "cat",
        "ls",
        "sed",
        "dirname",
        "basename",
        "uname",
        "tr",
        "head",
        "cut",
        "chmod",
        "mkdir",
        "rm",
        "mv",
        "cp",
        "awk",
        "find",
        "pwd",
    ):
        src = shutil.which(name)
        if src:
            dest = bin_dir / name
            if not dest.exists():
                dest.symlink_to(src)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    result = subprocess.run(
        ["bash", str(gate)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert "command not found" not in combined
    assert result.returncode == 0, combined
    assert "core_no_app_import_check OK" in result.stdout
