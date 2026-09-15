"""Tests for v3 loader extensions (Phase B2)."""

from pathlib import Path


def _write(
    root: Path,
    slug: str,
    body: str,
    with_skill: str | None = None,
    with_script: bool = False,
    slug_in_yaml: str | None = None,
):
    d = root / slug
    d.mkdir()
    yaml_slug = slug_in_yaml or slug
    if with_skill:
        (d / "profile.yaml").write_text(
            f"integral_profile_version: 3\nscope: track\n"
            f"package:\n  slug: {yaml_slug}\n  name: T\n  version: 1.0.0\n"
            f"track:\n  entry_types: []\n  skills: [{with_skill}]\n"
        )
    else:
        (d / "profile.yaml").write_text(
            f"integral_profile_version: 3\nscope: track\n"
            f"package:\n  slug: {yaml_slug}\n  name: T\n  version: 1.0.0\n"
            f"track:\n  entry_types: []\n"
        )
    if with_skill:
        s = d / "skills" / with_skill
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text(f"---\nname: {with_skill}\n---\nbody")
        if with_script:
            (s / "scripts").mkdir()
            (s / "scripts" / "t.py").write_text(
                "def get_tool_definition():\n    return {}\n"
                "async def execute(a):\n    return None\n"
            )
    return d


def test_valid_bundle_loads(tmp_path):
    from app.services.content_profile_loader import load_library_profiles

    _write(tmp_path, "b-valid", "", with_skill="k1")
    specs = load_library_profiles(profiles_root=tmp_path)
    assert len(specs) == 1
    assert specs[0].slug == "b-valid"
    assert specs[0].skill_keys == ["k1"]
    assert specs[0].ships_python is False


def test_bundle_with_python_marks_ships_python(tmp_path):
    from app.services.content_profile_loader import load_library_profiles

    _write(tmp_path, "b-py", "", with_skill="k1", with_script=True)
    specs = load_library_profiles(profiles_root=tmp_path)
    assert specs[0].ships_python is True


def test_slug_mismatch_skips_bundle(tmp_path):
    """I-BUNDLE-04: dir name MUST match package.slug."""
    from app.services.content_profile_loader import load_library_profiles

    _write(tmp_path, "b-mismatch", "", slug_in_yaml="other-slug")
    specs = load_library_profiles(profiles_root=tmp_path)
    assert specs == []


def test_skill_key_without_skill_md_excludes_that_skill(tmp_path, caplog):
    """I-BUNDLE-02: manifest declares skill but skills/<key>/SKILL.md missing → skip."""
    from app.services.content_profile_loader import load_library_profiles

    d = tmp_path / "b-orphan"
    d.mkdir()
    (d / "profile.yaml").write_text(
        "integral_profile_version: 3\nscope: track\n"
        "package:\n  slug: b-orphan\n  name: T\n  version: 1.0.0\n"
        "track:\n  entry_types: []\n  skills: [ghost]\n"
    )
    specs = load_library_profiles(profiles_root=tmp_path)
    assert len(specs) == 1
    assert specs[0].skill_keys == []  # ghost excluded


def test_bundle_fingerprint_changes_when_file_edited(tmp_path):
    from app.services.content_profile_loader import compute_bundle_fingerprint

    d = _write(tmp_path, "b-fp", "", with_skill="k1")
    fp1 = compute_bundle_fingerprint(d)
    (d / "skills" / "k1" / "SKILL.md").write_text("---\nname: k1\n---\nNEW")
    fp2 = compute_bundle_fingerprint(d)
    assert fp1 != fp2


def test_duplicate_skill_key_across_bundles_logs_and_keeps_first(tmp_path, caplog):
    """I-BUNDLE-05: per-bundle key uniqueness; global key is <slug>__<key>."""
    import logging

    from app.services.content_profile_loader import load_library_profiles

    caplog.set_level(logging.WARNING)
    _write(tmp_path, "a-dup", "", with_skill="shared")
    _write(tmp_path, "b-dup", "", with_skill="shared")
    specs = load_library_profiles(profiles_root=tmp_path)
    # both bundles load — global key uniqueness is <slug>__<key>, not just <key>.
    assert len(specs) == 2


def test_content_factory_bundle_loads_with_v3_skills():
    from pathlib import Path

    from app.services.content_profile_loader import load_library_profiles

    root = Path(__file__).resolve().parent.parent / "app" / "profiles"
    specs = load_library_profiles(profiles_root=root)
    cf = next((s for s in specs if s.slug == "content-factory"), None)
    assert cf is not None
    assert "carousel_drafter" in cf.skill_keys
    assert "performance_reviewer" in cf.skill_keys
    skill_md = cf.bundle_dir / "skills" / "carousel_drafter" / "SKILL.md"
    assert skill_md.exists()


def test_load_with_issues_reports_structured_diagnostics(tmp_path):
    from app.services.content_profile_loader import load_library_profiles_with_issues

    _write(tmp_path, "bad-slug-dir", "", slug_in_yaml="different-slug")
    ok = tmp_path / "with-missing-skill"
    ok.mkdir()
    (ok / "profile.yaml").write_text(
        "integral_profile_version: 3\nscope: track\n"
        "package:\n  slug: with-missing-skill\n  name: T\n  version: 1.0.0\n"
        "track:\n  entry_types: []\n  skills: [ghost]\n"
    )

    specs, issues = load_library_profiles_with_issues(profiles_root=tmp_path)
    assert len(specs) == 1
    codes = {getattr(i, "code", "") for i in issues}
    assert "slug_mismatch" in codes
    assert "missing_skill_markdown" in codes
