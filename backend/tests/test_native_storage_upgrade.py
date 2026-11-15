"""Offline maintenance must fail closed before launching a private database."""

import argparse
import hashlib
import zipfile
from pathlib import Path

import pytest

from app.services.storage_indexes import native_upgrade as upgrade

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.fixture
def native_home(tmp_path):
    """Retain a synthetic old installation outside real native homes."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    upgrade.write_private(
        home / "installation.json",
        {
            "contract_version": 1,
            "deployment_profile": "desktop_single_user",
            "installation_id": "synthetic-installation",
            "jwt_key": "retained-key",
        },
    )
    (home / "postgres").mkdir()
    (home / "postgres/PG_VERSION").write_text("16")
    return home


def arguments(home, **changes):
    """Construct explicit maintenance arguments with bounded fixture budgets."""
    values = {
        "action": "plan",
        "home": home,
        "wheel": home / "candidate.whl",
        "plan": home / "reviewed.json",
        "approved_hash": None,
        "max_indexes": 2000,
        "max_index_bytes": 2_000_000_000,
        "max_build_bytes": 3_000_000_000,
    }
    return argparse.Namespace(**{**values, **changes})


def test_public_cli_dispatches_offline_contract(monkeypatch):
    """Verify public cli dispatches offline contract."""
    from app import cli

    called = []
    monkeypatch.setattr(upgrade, "run_cli", lambda args: called.append(args) or 23)
    assert (
        cli.main(
            [
                "storage-upgrade",
                "apply",
                "--home",
                "/fixture",
                "--wheel",
                "/candidate.whl",
                "--plan",
                "/review.json",
                "--approved-hash",
                "a" * 64,
            ]
        )
        == 23
    )
    assert called[0].action == "apply"
    assert called[0].approved_hash == "a" * 64


@pytest.mark.parametrize(
    "change",
    [
        {"max_indexes": 0},
        {"max_build_bytes": None},
        {"max_index_bytes": -1},
        {"action": "apply", "approved_hash": "a" * 64},
    ],
)
def test_invalid_approval_or_budgets_never_start_database(
    native_home, monkeypatch, change
):
    """Verify invalid approval or budgets never start database."""
    monkeypatch.setattr(upgrade, "wheel_identity", lambda _: "b" * 64)
    monkeypatch.setattr(
        upgrade, "private_database", lambda _: pytest.fail("database started")
    )
    upgrade.write_private(native_home / "reviewed.json", {"version": 1})
    args = arguments(native_home, **change)
    if args.action == "plan":
        args.plan.unlink()
    with pytest.raises(ValueError):
        upgrade.execute(args)


def test_existing_plan_is_never_overwritten(native_home, monkeypatch):
    """Verify existing plan is never overwritten."""
    monkeypatch.setattr(upgrade, "wheel_identity", lambda _: "b" * 64)
    plan = native_home / "reviewed.json"
    plan.write_bytes(b"original reviewed bytes")
    with pytest.raises(ValueError, match="Never overwrite"):
        upgrade.execute(arguments(native_home))
    assert plan.read_bytes() == b"original reviewed bytes"


@pytest.mark.parametrize(
    "marker", ["recovery.json", "postgres/postmaster.pid", "database.json"]
)
def test_quarantine_and_stale_process_markers_prevent_helper(
    native_home, monkeypatch, marker
):
    """Verify quarantine and stale process markers prevent helper."""
    from app import local_runtime

    monkeypatch.setattr(local_runtime, "status", lambda _: {"state": "stopped"})
    monkeypatch.setattr(
        upgrade.subprocess, "Popen", lambda *_a, **_k: pytest.fail("helper started")
    )
    (native_home / marker).write_text("{}")
    with (
        pytest.raises((ValueError, local_runtime.RuntimeErrorDetail)),
        upgrade.private_database(native_home),
    ):
        pytest.fail("database yielded")
    assert (native_home / marker).read_text() == "{}"


def test_live_supervisor_prevents_maintenance(native_home, monkeypatch):
    """Verify live supervisor prevents maintenance."""
    from app import local_runtime

    monkeypatch.setattr(local_runtime, "status", lambda _: {"state": "running"})
    monkeypatch.setattr(
        upgrade.subprocess, "Popen", lambda *_a, **_k: pytest.fail("helper started")
    )
    with (
        pytest.raises(ValueError, match="Stop the owned runtime"),
        upgrade.private_database(native_home),
    ):
        pytest.fail("database yielded")


def test_external_storage_link_is_rejected(native_home, tmp_path):
    """Verify external storage link is rejected."""
    external = tmp_path / "external"
    external.mkdir()
    (native_home / "postgres/tablespace").symlink_to(external, target_is_directory=True)
    with (
        pytest.raises(ValueError, match="External database paths"),
        upgrade.private_database(native_home),
    ):
        pytest.fail("database yielded")


def test_dangling_temporary_link_cannot_redirect_credentials(native_home, tmp_path):
    """Verify dangling temporary link cannot redirect credentials."""
    target = tmp_path / "do-not-create"
    (native_home / "installation.tmp").symlink_to(target)
    with pytest.raises(ValueError, match="Unsafe maintenance destination"):
        upgrade.write_private(native_home / "installation.json", {"replaced": True})
    assert not target.exists()
    assert (
        upgrade.read_private(native_home / "installation.json")["jwt_key"]
        == "retained-key"
    )


def test_wheel_must_match_executing_engine(tmp_path, monkeypatch):
    """Verify wheel must match executing engine."""
    wheel = tmp_path / "candidate.whl"
    root = Path(upgrade.__file__).resolve().parents[2]
    files = [
        *(root / "services/storage_indexes").glob("*.py"),
        root / "services/storage_indexes/approved.json",
        *[
            root / name
            for name in (
                "cli.py",
                "local_runtime.py",
                "local_permissions.py",
                "local_postgres.py",
            )
        ],
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        for source in files:
            archive.write(source, "app/" + source.relative_to(root).as_posix())
    assert (
        upgrade.wheel_identity(wheel) == hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    alias = tmp_path / "linked-package"
    alias.symlink_to(Path(upgrade.__file__).resolve().parent, target_is_directory=True)
    monkeypatch.setattr(upgrade, "__file__", str(alias / "native_upgrade.py"))
    assert (
        upgrade.wheel_identity(wheel) == hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for source in files:
            archive.writestr(
                "app/" + source.relative_to(root).as_posix(),
                b"mismatched" if source.name == "catalog.py" else source.read_bytes(),
            )
    with pytest.raises(ValueError, match="does not match"):
        upgrade.wheel_identity(wheel)


def test_unexpected_failure_redacts_connection_secrets(monkeypatch, capsys):
    """Verify unexpected failure redacts connection secrets."""

    def fail(_):
        raise RuntimeError("postgres://secret-user:secret-password@private-host/db")

    monkeypatch.setattr(upgrade, "execute", fail)
    assert upgrade.run_cli(argparse.Namespace()) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "RuntimeError" in output.err
    assert "secret" not in output.err
    assert "private-host" not in output.err
