"""Failure-envelope regression tests for the dependency audit gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / ".ci" / "dependency_audit.sh"


@pytest.mark.parametrize(
    "document",
    [
        '{"error":{"code":"E401","summary":"registry unavailable"}}',
        "{malformed-json",
        "",
    ],
)
def test_frontend_audit_error_or_malformed_output_fails_closed(tmp_path, document):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        "#!/bin/sh\n" "printf '%s' \"$AUDIT_FIXTURE\"\n" 'exit "${AUDIT_EXIT:-1}"\n'
    )
    npm.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUDIT_FIXTURE": document,
        "AUDIT_EXIT": "1",
    }
    result = subprocess.run(
        ["bash", str(AUDIT_SCRIPT), "frontend"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stderr


def test_successful_frontend_audit_document_is_accepted(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        "#!/bin/sh\n"
        "printf '%s' '{\"metadata\":{},\"vulnerabilities\":{}}'\n"
        "exit 0\n"
    )
    npm.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(AUDIT_SCRIPT), "frontend"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean (no fixable" in result.stdout


@pytest.mark.parametrize("audit_exit", [0, 7])
def test_backend_audit_uses_pinned_lock_without_bootstrapping_pip(tmp_path, audit_exit):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' 'example==1.2.3'\n")
    uv.chmod(0o755)
    pip_audit = fake_bin / "pip-audit"
    pip_audit.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in *' --no-deps '* ) ;; *) exit 20 ;; esac\n"
        "case \" $* \" in *' --disable-pip '* ) ;; *) exit 21 ;; esac\n"
        'exit "${AUDIT_EXIT:-0}"\n'
    )
    pip_audit.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUDIT_EXIT": str(audit_exit),
    }
    result = subprocess.run(
        ["bash", str(AUDIT_SCRIPT), "backend"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if audit_exit == 0:
        assert result.returncode == 0, result.stderr
        assert "clean." in result.stdout
    else:
        assert result.returncode != 0
        assert "FAILED" in result.stderr
