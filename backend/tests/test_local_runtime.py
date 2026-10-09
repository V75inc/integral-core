"""Local lifecycle boundaries: identity, configuration and recoverable data."""

import io
import json
import os
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from app import cli
from app import local_runtime as runtime


def test_concurrent_initialization_preserves_one_identity(tmp_path):
    """Concurrent initialization preserves one identity."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        configs = list(pool.map(lambda _: runtime.initialize(tmp_path), range(8)))
    assert all(config == configs[0] for config in configs)
    assert runtime.initialize(tmp_path) == configs[0]
    if os.name != "nt":
        assert (tmp_path / "installation.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body", ["{}", "", "[]", "not-json"])
def test_existing_empty_or_damaged_control_file_never_generates_new_keys(
    tmp_path, body
):
    path = tmp_path / "installation.json"
    path.write_text(body)
    with pytest.raises(runtime.RuntimeErrorDetail):
        runtime.initialize(tmp_path)
    assert path.read_text() == body


@pytest.mark.parametrize(
    "key",
    [
        "jwt_key",
        "credential_key",
        "oauth_key",
        "database_password",
        "control_token",
        "installation_id",
    ],
)
def test_existing_incomplete_installation_refuses_without_replacing_keys(tmp_path, key):
    config = runtime.initialize(tmp_path)
    config.pop(key)
    runtime.private_json(tmp_path / "installation.json", config)
    original = (tmp_path / "installation.json").read_bytes()
    with pytest.raises(
        runtime.RuntimeErrorDetail, match=f"Installation {key} is missing or invalid"
    ):
        runtime.initialize(tmp_path)
    assert (tmp_path / "installation.json").read_bytes() == original


@pytest.mark.parametrize("key", ["credential_key", "oauth_key"])
def test_malformed_managed_encryption_key_is_actionable_and_never_rotated(
    tmp_path, key
):
    config = runtime.initialize(tmp_path)
    config[key] = "malformed-key-that-must-never-be-printed"
    runtime.private_json(tmp_path / "installation.json", config)
    with pytest.raises(runtime.RuntimeErrorDetail) as error:
        runtime.initialize(tmp_path)
    assert config[key] not in str(error.value)
    assert runtime.read_json(tmp_path / "installation.json") == config


def test_reused_pid_never_counts_as_our_runtime(tmp_path):
    """Reused pid never counts as our runtime."""
    process = psutil.Process()
    runtime.private_json(
        tmp_path / "runtime.json",
        {
            "pid": process.pid,
            "created": process.create_time() - 1,
            "state": "ready",
        },
    )
    assert runtime.status(tmp_path)["state"] == "stopped"
    assert runtime._process(runtime.read_json(tmp_path / "runtime.json")) is None


def test_client_shutdown_does_not_stop_a_replacement_process(tmp_path, monkeypatch):
    """A desktop cannot stop a runtime that replaced the instance it started."""
    process = psutil.Process()
    runtime.private_json(
        tmp_path / "runtime.json",
        {
            "pid": process.pid,
            "created": process.create_time(),
            "state": "starting",
        },
    )

    def forbidden_stop(_home):
        raise AssertionError("replacement runtime must remain alive")

    monkeypatch.setattr(runtime, "_stop", forbidden_stop)
    assert (
        runtime.stop(
            tmp_path,
            expected_pid=process.pid,
            expected_created=process.create_time() - 1,
        )["state"]
        == "starting"
    )


def test_descriptor_never_exports_keys(tmp_path, capsys):
    """Descriptor never exports keys."""
    config = runtime.initialize(tmp_path)
    assert cli.main(["status", "--home", str(tmp_path), "--json"]) == 0
    descriptor = capsys.readouterr().out
    assert json.loads(descriptor)["contract_version"] == 1
    for key in ("jwt_key", "credential_key", "control_token", "database_password"):
        assert config[key] not in descriptor


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", "file"),
        ("/absolute", "file"),
        ("files/link", "symlink"),
        ("C:\\escape", "file"),
    ],
)
def test_restore_refuses_paths_and_links_without_partial_install(tmp_path, name, kind):
    """Restore refuses paths and links without partial install."""
    archive_path = tmp_path / "bad.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        entry = tarfile.TarInfo(name)
        if kind == "symlink":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            archive.addfile(entry)
        else:
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"bad"))
    with pytest.raises(runtime.RuntimeErrorDetail):
        runtime.restore(tmp_path / "restored", archive_path)
    assert not (tmp_path / "restored").exists()


def test_backup_restore_retains_identity_keys_and_files(tmp_path):
    """Backup restore retains identity keys and files."""
    home = tmp_path / "source"
    config = runtime.initialize(home)
    (home / "files" / "note.txt").write_text("retained knowledge")
    config["runtime_python"] = "/old/machine/python"
    runtime.private_json(home / "installation.json", config)
    archive = runtime.backup(home, tmp_path / "backup.tar.gz")
    restored = tmp_path / "restored"
    runtime.restore(restored, archive)
    actual = runtime.initialize(restored)
    assert actual == {
        key: value for key, value in config.items() if key != "runtime_python"
    }
    assert (restored / "files/note.txt").read_text() == "retained knowledge"
    with pytest.raises(runtime.RuntimeErrorDetail):
        runtime.restore(restored, archive)


def test_managed_environment_excludes_checkout_dotenv(tmp_path):
    """Managed environment excludes checkout dotenv."""
    custom = tmp_path / "settings.env"
    custom.write_text("DEBUG=false\nADMIN_EMAIL=managed@example.test\n")
    env = dict(os.environ)
    for name in (
        "DEBUG",
        "ADMIN_EMAIL",
        "INTEGRAL_NATIVE_MODEL",
        "INTEGRAL_NATIVE_HARNESS_ENABLED",
    ):
        env.pop(name, None)
    env["INTEGRAL_ENV_FILE"] = str(custom)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pytest; from app.main import settings; import os,json; "
            "print(json.dumps({'debug':settings.DEBUG,'admin':os.getenv('ADMIN_EMAIL'),"
            "'model':os.getenv('INTEGRAL_NATIVE_MODEL'),'toggle':os.getenv('INTEGRAL_NATIVE_HARNESS_ENABLED')}))",
        ],
        env=env,
        cwd=Path(__file__).parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "debug": False,
        "admin": "managed@example.test",
        "model": None,
        "toggle": None,
    }


def test_managed_child_keeps_real_auth_and_postgres(tmp_path, monkeypatch):
    """Managed child keeps real auth and postgres."""
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DEBUG", "true")
    config = runtime.initialize(tmp_path)
    env = runtime._environment(
        tmp_path, config, "postgresql://private", 4000, 9006, None
    )
    assert "TESTING" not in env
    assert env["DEBUG"] == "false"
    assert env["RATE_LIMIT_DISABLED"] == "0"
    assert env["JVSPATIAL_DB_TYPE"] == "postgres"
    assert env["INTEGRAL_CORE_ONLY"] == "1"


def test_backup_preserves_external_apps_and_relocates_the_path(tmp_path):
    """External package definitions travel with the restored installation."""
    home = tmp_path / "home"
    apps = tmp_path / "business-apps"
    apps.mkdir()
    (apps / "definition.yaml").write_text("retained definition")
    config = runtime.initialize(home)
    config["apps_path"] = str(apps)
    runtime.private_json(home / "installation.json", config)
    archive = runtime.backup(home, tmp_path / "apps-backup.tar.gz")
    restored = tmp_path / "restored"
    runtime.restore(restored, archive)
    assert runtime.read_json(restored / "installation.json")["apps_path"] == str(
        restored / "integral-apps"
    )
    assert (
        restored / "integral-apps/definition.yaml"
    ).read_text() == "retained definition"


@pytest.mark.parametrize("identity", [None, "jvagent"])
def test_up_cannot_reopen_a_legacy_runtime(tmp_path, monkeypatch, identity):
    monkeypatch.setattr(
        runtime,
        "status",
        lambda _home: {"state": "ready", "harness_provider_id": identity},
    )
    with pytest.raises(runtime.RuntimeErrorDetail, match="pre-native Core"):
        runtime.start(tmp_path, open_browser=False)


def test_stopped_descriptor_identifies_builtin_native_default(tmp_path):
    result = runtime.status(tmp_path)
    assert result["default_harness_provider_id"] == "integral_native"
    assert result["harness_provider_id"] is None
