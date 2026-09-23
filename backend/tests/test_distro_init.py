"""integral init writes a distro the package loader can see."""

from pathlib import Path

import pytest

from app.cli import init_distro, main


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
    assert len(env.split("JVSPATIAL_JWT_SECRET_KEY=", 1)[1].splitlines()[0]) >= 32
    assert (dest / "README.md").is_file()
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


def test_init_rejects_a_bad_slug(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        init_distro(tmp_path, slug="Studio Equipment")


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
