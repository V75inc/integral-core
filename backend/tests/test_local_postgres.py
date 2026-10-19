"""Windows database bootstrap authenticates before its first TCP listener."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import local_postgres as postgres


def test_initialization_installs_scram_without_password_in_argv(tmp_path, monkeypatch):
    data = tmp_path / "postgres"
    data.mkdir()
    calls = []
    password = "a" * 64

    def run(argv, **kwargs):
        assert password not in repr(argv)
        assert "--auth-host=scram-sha-256" in argv
        name = next(a.split("=", 1)[1] for a in argv if a.startswith("--pwfile="))
        assert Path(name).read_text() == password + "\n"
        assert kwargs["check"] is True
        calls.append(argv)
        (data / "PG_VERSION").write_text("16")

    monkeypatch.setattr(postgres.subprocess, "run", run)
    postgres.prepare_windows_database(tmp_path / "bin", tmp_path, data, password)
    assert len(calls) == 1
    assert not list(tmp_path.glob(".postgres-password-*"))
    assert "scram-sha-256" in (data / "pg_hba.conf").read_text()
    assert "host all all 127.0.0.1/32 trust" not in (data / "pg_hba.conf").read_text()


def test_existing_database_is_not_reinitialized(tmp_path, monkeypatch):
    (tmp_path / "PG_VERSION").write_text("16")
    (tmp_path / "record").write_text("retained")
    monkeypatch.setattr(
        postgres.subprocess, "run", lambda *a, **k: pytest.fail("reinitialized")
    )
    postgres.prepare_windows_database(tmp_path, tmp_path, tmp_path, "b" * 64)
    assert (tmp_path / "record").read_text() == "retained"
    assert (tmp_path / "pg_hba.conf").read_text() == postgres.HBA


def test_failed_initialization_removes_private_password_file(tmp_path, monkeypatch):
    def fail(*_a, **_k):
        raise OSError("synthetic initialization error")

    monkeypatch.setattr(postgres.subprocess, "run", fail)
    with pytest.raises(OSError):
        postgres.prepare_windows_database(tmp_path, tmp_path, tmp_path, "c" * 64)
    assert not list(tmp_path.glob(".postgres-password-*"))


@pytest.mark.parametrize("status", [0, 2])
def test_password_probe_ignores_ambient_credentials_and_fails_closed(
    tmp_path, monkeypatch, status
):
    monkeypatch.setenv("PGPASSWORD", "ambient-synthetic-secret")
    monkeypatch.setenv("PGSERVICE", "ambient-service")
    monkeypatch.setenv("PGSERVICEFILE", "ambient-service-file")

    def run(argv, **kwargs):
        assert "--no-password" in argv
        assert "PGPASSWORD" not in kwargs["env"]
        assert "PGSERVICE" not in kwargs["env"]
        assert "PGSERVICEFILE" not in kwargs["env"]
        assert kwargs["env"]["PGPASSFILE"] == postgres.os.devnull
        return SimpleNamespace(returncode=status)

    monkeypatch.setattr(postgres.subprocess, "run", run)
    if status == 0:
        with pytest.raises(RuntimeError, match="without a password"):
            postgres.require_password(
                tmp_path, "postgresql://postgres@127.0.0.1:1234/postgres"
            )
    else:
        postgres.require_password(
            tmp_path, "postgresql://postgres@127.0.0.1:1234/postgres"
        )


def test_windows_uri_does_not_override_the_environment_with_empty_password():
    uri = postgres.passwordless_uri("postgresql://postgres:@127.0.0.1:5432/postgres")
    assert uri == "postgresql://postgres@127.0.0.1:5432/postgres"
    argv = postgres.psql_command(Path("bin"), uri)
    assert argv[1:] == ["--no-password", "--set=ON_ERROR_STOP=1", "--dbname=" + uri]
