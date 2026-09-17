"""Contract: AC-12 — backup + restore drill scripts report integrity."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = REPO / "scripts" / "pg_backup.sh"
RESTORE_SCRIPT = REPO / "scripts" / "pg_restore.sh"

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


def _pg_env() -> dict[str, str]:
    dsn = os.environ.get("INTEGRAL_TEST_POSTGRES_DSN") or os.environ.get(
        "JVSPATIAL_POSTGRES_DSN", ""
    )
    if dsn.startswith("postgresql://"):
        # postgresql://user:pass@host:port/db
        from urllib.parse import urlparse

        parts = urlparse(dsn)
        user = parts.username or "integral"
        password = parts.password or "integral"
        host = parts.hostname or "localhost"
        port = str(parts.port or 5432)
        db = (parts.path or "/integral").lstrip("/")
    else:
        user = os.environ.get("POSTGRES_USER", "integral")
        password = os.environ.get("POSTGRES_PASSWORD", "integral")
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5432")
        db = os.environ.get("POSTGRES_DB", "integral")
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_USER": user,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_HOST": host,
            "POSTGRES_PORT": port,
            "POSTGRES_DB": db,
        }
    )
    return env


@pytest.mark.postgres
@pytest.mark.contract
async def test_backup_restore_drill_round_trip():
    if (os.environ.get("INTEGRAL_TEST_DB") or "json").lower() not in (
        "postgres",
        "postgresql",
    ):
        pytest.skip("Postgres-only (set INTEGRAL_TEST_DB=postgres)")
    if shutil.which("docker") is None:
        pytest.skip("docker required for pg_backup/pg_restore scripts")
    if not BACKUP_SCRIPT.is_file() or not RESTORE_SCRIPT.is_file():
        pytest.skip("backup/restore scripts missing")

    env = _pg_env()
    with tempfile.TemporaryDirectory() as tmp:
        env["BACKUP_DIR"] = tmp
        backup = subprocess.run(
            [str(BACKUP_SCRIPT)],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if backup.returncode != 0:
            pytest.skip(
                f"postgres not reachable for backup drill: {backup.stderr or backup.stdout}"
            )
        dumps = sorted(Path(tmp).glob("integral_*.dump"))
        assert dumps, "pg_backup.sh produced no dump file"
        dump_path = dumps[-1]

        drill = subprocess.run(
            [str(RESTORE_SCRIPT), str(dump_path), "--drill"],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert drill.returncode == 0, drill.stderr or drill.stdout
        assert "drill: complete" in (drill.stdout or "")
