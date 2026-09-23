"""integral init writes a distro the package loader can see."""

import base64
import os
from pathlib import Path

import pytest

from app.cli import init_distro, main
from app.config import load_integral_env_files


def test_init_writes_env_readme_and_matching_slug(tmp_path: Path) -> None:
    dest = init_distro(
        tmp_path / "my-integral", slug="studio-equipment", name="Studio Desk"
    )
    manifest = (
        dest / "integral-apps" / "studio-equipment" / "operational-model.yaml"
    ).read_text()
    env = (dest / ".env").read_text()
    assert "slug: studio-equipment" in manifest
    assert "name: Studio Desk" in manifest
    assert f"INTEGRAL_PACKAGE_PATHS={dest / 'integral-apps'}" in env
    assert "INTEGRAL_CORE_ONLY=0" in env
    assert "OPENAI_API_KEY=" in env
    assert "INTEGRAL_AGENT_KEY_MODE=hybrid" in env
    enc = env.split("INTEGRAL_CREDENTIAL_ENC_KEY=", 1)[1].splitlines()[0]
    assert len(base64.b64decode(enc)) == 32
    assert len(env.split("JVSPATIAL_JWT_SECRET_KEY=", 1)[1].splitlines()[0]) >= 32
    readme = (dest / "README.md").read_text()
    assert "integral web" in readme
    assert (
        dest / "integral-apps" / "studio-equipment" / "tools" / ".gitkeep"
    ).is_file()
    assert (
        dest / "integral-apps" / "studio-equipment" / "skills" / ".gitkeep"
    ).is_file()
    assert (
        dest / "integral-apps" / "studio-equipment" / "views" / ".gitkeep"
    ).is_file()
    assert ".env" in (dest / ".gitignore").read_text()


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    init_distro(tmp_path, slug="starter")
    with pytest.raises(FileExistsError):
        init_distro(tmp_path, slug="starter")
    init_distro(tmp_path, slug="starter", force=True)


def test_cwd_dotenv_fills_unset_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "INTEGRAL_SMOKE_DOTENV_MARKER=from-cwd\n", encoding="utf-8"
    )
    monkeypatch.delenv("INTEGRAL_SMOKE_DOTENV_MARKER", raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        load_integral_env_files()
        assert os.environ.get("INTEGRAL_SMOKE_DOTENV_MARKER") == "from-cwd"
    finally:
        os.environ.pop("INTEGRAL_SMOKE_DOTENV_MARKER", None)


def test_init_rejects_a_bad_slug(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        init_distro(tmp_path, slug="Studio Equipment")


def test_init_without_slug_is_a_blank_distro(tmp_path: Path) -> None:
    dest = init_distro(tmp_path / "my-integral")
    apps = dest / "integral-apps"
    assert (apps / ".gitkeep").is_file()
    assert list(apps.glob("*/operational-model.yaml")) == []
    assert f"INTEGRAL_PACKAGE_PATHS={apps}" in (dest / ".env").read_text()
    assert "No App is included" in (dest / "README.md").read_text()


def test_cli_blank_init_does_not_write_starter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["init", str(tmp_path / "box")])
    captured = capsys.readouterr()
    assert code == 0
    assert "Blank distro" in captured.out
    assert not (tmp_path / "box" / "integral-apps" / "starter").exists()


def test_cli_main_writes_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["init", "--slug", "starter", str(tmp_path / "box")])
    captured = capsys.readouterr()
    assert code == 0
    assert "Wrote distro" in captured.out
    assert (
        tmp_path / "box" / "integral-apps" / "starter" / "operational-model.yaml"
    ).is_file()
