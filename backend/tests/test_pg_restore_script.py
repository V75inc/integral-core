"""Recovery tooling must never classify a failed restore as successful."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
RESTORE = Path(__file__).resolve().parents[2] / "scripts" / "pg_restore.sh"


@pytest.fixture
def restore_environment(tmp_path):
    """Replace Docker with a deterministic local client fixture, never a DB."""
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if "pg_restore" in args:
    if "--list" in args:
        raise SystemExit(0)
    raise SystemExit(int(os.environ["TEST_RESTORE_EXIT"]))
if "psql" in args:
    sql = args[args.index("-c") + 1] if "-c" in args else ""
    if "to_regclass" in sql:
        print("t")
    elif "SELECT count(*)" in sql:
        print("1")
    elif "SELECT md5" in sql:
        print("matching-fixture-identity")
    raise SystemExit(0)
raise SystemExit(99)
"""
    )
    docker.chmod(0o755)
    dump = tmp_path / "fixture.dump"
    dump.write_bytes(b"fixture archive; no actual PostgreSQL data")
    env = os.environ.copy()
    env.update(
        PATH=str(binary) + os.pathsep + env["PATH"],
        POSTGRES_USER="restore-fixture",
        POSTGRES_PASSWORD="fixture-password",
        POSTGRES_DB="fixture_source",
        RESTORE_CONFIRM="fixture_source",
    )
    return dump, env


@pytest.mark.parametrize("mode", [[], ["--into", "fixture_target"], ["--drill"]])
@pytest.mark.parametrize("status", [0, 42])
def test_restore_preserves_client_failure_status(restore_environment, mode, status):
    """All restore modes fail closed even when counts and identities would match."""
    dump, env = restore_environment
    env["TEST_RESTORE_EXIT"] = str(status)
    result = subprocess.run(
        ["bash", str(RESTORE), str(dump), *mode],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if status:
        assert result.returncode != 0, result.stdout
        assert "drill: complete" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr or result.stdout
        if mode == ["--drill"]:
            assert "drill: complete" in result.stdout
    if mode == ["--drill"]:
        assert "dropping scratch database" in result.stdout
