"""Validated source reuse must survive profile expiry without stale artifacts."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import operational_model_library_sync as cache
from app.services import operational_model_loader as loader

pytestmark = pytest.mark.smoke


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Build an isolated validated package source and count compiler calls."""
    root = tmp_path / "packages"
    bundle = root / "sample"
    skill = bundle / "skills" / "inspect" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: inspect\ndescription: Inspect records.\n---\nOriginal body."
    )
    (bundle / "operational-model.yaml").write_text(
        "integral_operational_model_version: 3\nscope: track\n"
        "package:\n  slug: sample\n  name: Sample\n  version: 1.0.0\n"
        "track:\n  entry_types: []\n  skills: [inspect]\n"
    )
    monkeypatch.setattr(loader, "_PROFILES_ROOT", root)
    monkeypatch.delenv("INTEGRAL_OPERATIONAL_MODEL_PUBKEY", raising=False)
    monkeypatch.delenv("INTEGRAL_PLUGIN_PUBKEY", raising=False)
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    cache.reset_library_operational_models_cache_for_testing()
    calls = []
    original = cache.load_library_operational_models_with_issues

    def load():
        calls.append(threading.get_ident())
        return original()

    monkeypatch.setattr(cache, "load_library_operational_models_with_issues", load)
    yield SimpleNamespace(root=root, bundle=bundle, skill=skill, calls=calls)
    cache.reset_library_operational_models_cache_for_testing()


def test_concurrent_readers_share_one_validation(library):
    """Concurrent turns must not duplicate signature and manifest validation."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.load_library_bundle_index(), range(8)))
    assert len(library.calls) == 1
    assert len({token for token, _ in results}) == 1
    assert all(index["sample"].bundle_dir == library.bundle for _, index in results)


@pytest.mark.parametrize(
    "artifact", ["manifest", "skill", "python", "signature", "bytecode"]
)
def test_artifact_change_invalidates_shared_validation(library, artifact):
    """Every signed artifact change invalidates the shared source result."""
    before, _ = cache.load_library_bundle_index()
    target = {
        "manifest": library.bundle / "operational-model.yaml",
        "skill": library.skill,
        "python": library.bundle / "tools" / "helper.py",
        "signature": library.bundle / "signature.bin",
        "bytecode": library.bundle / "__pycache__" / "helper.cpython-311.pyc",
    }[artifact]
    target.parent.mkdir(exist_ok=True)
    with target.open("a") as out:
        out.write("\n# changed\n")
    after, _ = cache.load_library_bundle_index()
    assert after != before
    assert len(library.calls) == 2


def test_signed_bytecode_change_cannot_reuse_trusted_bundle(library, monkeypatch):
    """Cache invalidation covers all bytes used by existing signature validation."""
    from nacl.encoding import Base64Encoder
    from nacl.signing import SigningKey

    from app.services.operational_model_signature import compute_bundle_payload

    tools = library.bundle / "tools"
    tools.mkdir()
    (tools / "helper.py").write_text("# signed helper\n")
    bytecode = tools / "__pycache__" / "helper.cpython-311.pyc"
    bytecode.parent.mkdir()
    bytecode.write_bytes(b"initial bytecode")
    key = SigningKey.generate()
    monkeypatch.setenv(
        "INTEGRAL_OPERATIONAL_MODEL_PUBKEY",
        key.verify_key.encode(encoder=Base64Encoder).decode(),
    )
    (library.bundle / "signature.bin").write_bytes(
        key.sign(compute_bundle_payload(library.bundle)).signature
    )
    before, specs = cache.load_library_bundle_index()
    assert "sample" in specs
    bytecode.write_bytes(b"changed bytecode")
    after, specs = cache.load_library_bundle_index()
    assert after != before
    assert "sample" not in specs
    assert len(library.calls) == 2


def test_root_order_changes_revalidate_and_preserve_first_source(library, monkeypatch):
    """Package root precedence remains part of validated source identity."""
    from pathlib import Path

    second = library.root.parent / "second"
    alternate = second / "sample"
    alternate.mkdir(parents=True)
    (alternate / "operational-model.yaml").write_text(
        (library.bundle / "operational-model.yaml").read_text()
    )
    monkeypatch.setattr(
        loader, "_PROFILES_ROOT", Path(loader.__file__).parent.parent / "packages"
    )
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", f"{library.root},{second}")
    first_token, index = cache.load_library_bundle_index()
    assert index["sample"].bundle_dir == library.bundle
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", f"{second},{library.root}")
    second_token, index = cache.load_library_bundle_index()
    assert first_token != second_token
    assert index["sample"].bundle_dir == alternate


def test_unstable_package_update_never_returns_previous_specs(library, monkeypatch):
    """An update racing with validation fails closed instead of serving stale specs."""
    cache.load_library_bundle_index()
    original = cache.load_library_operational_models_with_issues

    def racing_load():
        result = original()
        library.skill.write_text(library.skill.read_text() + "\nchanged again")
        return result

    library.skill.write_text(library.skill.read_text() + "\ninitial update")
    monkeypatch.setattr(
        cache, "load_library_operational_models_with_issues", racing_load
    )
    with pytest.raises(RuntimeError, match="changed during validation"):
        cache.load_library_bundle_index()
    assert cache._CACHED_LIBRARY_SPECS is None


def test_same_size_replacement_with_preserved_mtime_invalidates(library):
    """Publishers preserving mtime must not hide changed skill bodies."""
    before, _ = cache.load_library_bundle_index()
    stat = library.skill.stat()
    library.skill.write_text(library.skill.read_text().replace("Original", "Modified"))
    os.utime(library.skill, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after, _ = cache.load_library_bundle_index()
    assert before != after
    assert len(library.calls) == 2


def test_removed_skill_is_not_retained_in_cached_specs(library):
    """Deleted declared skills disappear without restarting the process."""
    _, index = cache.load_library_bundle_index()
    assert index["sample"].skill_keys == ["inspect"]
    library.skill.unlink()
    _, index = cache.load_library_bundle_index()
    assert index["sample"].skill_keys == []


def test_public_key_and_plugin_registry_changes_revalidate(library, monkeypatch):
    """Trust and plugin changes cannot reuse an earlier validation result."""
    from app.services import operational_model_field_types as fields

    original, _ = cache.load_library_bundle_index()
    monkeypatch.setenv("INTEGRAL_OPERATIONAL_MODEL_PUBKEY", "a-new-key")
    changed_key, _ = cache.load_library_bundle_index()
    assert original != changed_key
    version = fields.registry_version()
    monkeypatch.setattr(fields, "registry_version", lambda: version + 1)
    changed_plugin, _ = cache.load_library_bundle_index()
    assert changed_plugin != changed_key
    assert len(library.calls) == 3


@pytest.mark.asyncio
async def test_signature_tampering_cannot_reenter_through_recorded_path(
    library, monkeypatch
):
    """A rejected signature cannot be bypassed through path fallback."""
    from nacl.encoding import Base64Encoder
    from nacl.signing import SigningKey

    from app.agentive.workspace_agent_profile import (
        _resolve_bundle_dir_async,
        _skill_to_overlay_doc,
    )
    from app.services.operational_model_signature import compute_bundle_payload

    tools = library.bundle / "tools"
    tools.mkdir()
    (tools / "helper.py").write_text("# signed helper\n")
    key = SigningKey.generate()
    monkeypatch.setenv(
        "INTEGRAL_OPERATIONAL_MODEL_PUBKEY",
        key.verify_key.encode(encoder=Base64Encoder).decode(),
    )
    (library.bundle / "signature.bin").write_bytes(
        key.sign(compute_bundle_payload(library.bundle)).signature
    )
    app = SimpleNamespace(
        source_operational_model_slug="sample",
        installed_from_library_id=None,
        metadata={"bundle_dir_path": str(library.bundle)},
    )
    assert await _resolve_bundle_dir_async(app) == library.bundle
    assert library.calls[-1] != threading.get_ident()
    library.skill.write_text(library.skill.read_text() + "\nTampered body.")
    assert await _resolve_bundle_dir_async(app) is None
    skill = SimpleNamespace(
        key="inspect",
        origin="bundle",
        kind="declarative",
        enabled=True,
        prompt_template_ref="skills/inspect/SKILL.md",
        body_override=None,
        tools_required=[],
    )
    assert (
        _skill_to_overlay_doc(
            skill, app_slug="sample", bundle_dir=None, allow_disk_fallback=False
        )
        is None
    )


@pytest.mark.asyncio
async def test_profile_expiry_and_focus_do_not_recompile_library(library, monkeypatch):
    """Profile rebuilding reuses sources while file changes refresh bodies."""
    from app.agentive import workspace_agent_profile as profiles
    from app.agentive.services import skill_registry

    app = SimpleNamespace(
        id="app",
        name="Sample",
        workspace_id="workspace",
        updated_at="1",
        source_operational_model_slug="sample",
        metadata={},
        settings={},
    )
    skill = SimpleNamespace(
        id="skill",
        app_id="app",
        key="inspect",
        origin="bundle",
        enabled=True,
        kind="declarative",
        updated_at="1",
        private=False,
        body_override=None,
        prompt_template_ref="skills/inspect/SKILL.md",
        tools_required=[],
    )

    allowed = True
    permission_reads = []

    async def permitted_apps(*args: object, **kwargs: object):
        permission_reads.append(allowed)
        return [app] if allowed else []

    async def apps(*args: object, **kwargs: object):
        return [app]

    async def skills(*args: object, **kwargs: object):
        return [skill] if allowed else []

    async def empty(*args: object, **kwargs: object):
        return []

    monkeypatch.setattr(
        "app.services.agent_scope.callable_apps_for_scope", permitted_apps
    )
    monkeypatch.setattr(profiles.App, "find", apps)
    monkeypatch.setattr(profiles.Skill, "find", empty)
    monkeypatch.setattr(skill_registry, "get_callable_skills", skills)
    monkeypatch.setattr(profiles, "_workspace_connectors", empty)
    profiles.invalidate_workspace_profile("workspace")
    first = await profiles.compose_workspace_agent_profile(
        "workspace", user_id="reader"
    )
    assert len(first.overlay_skill_docs) == 1
    warm = await profiles.compose_workspace_agent_profile("workspace", user_id="reader")
    assert warm is first
    # Expire the timestamp without changing source or waiting a minute.
    for key, (revision, profile, timestamp) in list(profiles._profile_cache.items()):
        if key[0] == "workspace":
            profiles._profile_cache[key] = (
                revision,
                profile,
                timestamp - profiles._PROFILE_CACHE_TTL_SECONDS - 1,
            )
    expired = await profiles.compose_workspace_agent_profile(
        "workspace", user_id="reader"
    )
    focused = await profiles.compose_workspace_agent_profile(
        "workspace", user_id="reader", focused_app_id="app"
    )
    assert (
        first.overlay_skill_docs
        == expired.overlay_skill_docs
        == focused.overlay_skill_docs
    )
    assert len(library.calls) == 1
    library.skill.write_text(library.skill.read_text().replace("Original", "Updated"))
    updated = await profiles.compose_workspace_agent_profile(
        "workspace", user_id="reader"
    )
    assert updated.profile_version != first.profile_version
    assert "Updated body" in updated.overlay_skill_docs[0].body
    assert len(library.calls) == 2

    allowed = False
    revoked = await profiles.compose_workspace_agent_profile(
        "workspace", user_id="reader"
    )
    assert revoked.apps == () and revoked.overlay_skill_docs == ()
    assert permission_reads == [True, True, True, True, True, False]
    assert len(library.calls) == 2
