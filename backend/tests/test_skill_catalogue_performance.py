"""Skills discovery must not compile the catalogue per skill or block API work."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from app.agentive.services import agent_skills as service

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_list_reuses_validated_sources_and_keeps_disk_work_off_loop(
    tmp_path, monkeypatch
):
    """Share source work across skills while checking caller access afresh."""
    from app.services import operational_model_library_sync as library
    from app.services import skill_format

    owner_thread = threading.get_ident()
    apps = [
        SimpleNamespace(id=f"app-{i}", source_operational_model_slug=f"sample-{i}")
        for i in range(2)
    ]
    skills = []
    for i in range(20):
        app = apps[i % 2]
        key = f"inspect_{i}"
        skill_dir = tmp_path / app.id / "skills" / key
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {key}\ndescription: Disk description {i}\n"
            "allowed-tools: integral_list_apps\n---\nRead only.\n"
        )
        skills.append(
            SimpleNamespace(
                id=f"skill-{i}",
                key=key,
                app_id=app.id,
                origin="bundle",
                description="Saved description" if i == 0 else "",
                prompt_template_ref=f"skills/{key}/SKILL.md",
            )
        )
    allowed = True
    source_reads = []
    path_reads = []
    document_reads = []
    specs = {app.id: app for app in apps}

    def source_index():
        assert threading.get_ident() != owner_thread
        time.sleep(0.02)
        source_reads.append(True)
        return "revision", specs

    async def resolve(app, *, bundle_specs):
        assert bundle_specs is specs
        path_reads.append(app.id)
        return tmp_path / app.id

    original_parse = skill_format.parse_skill_document

    def parse(path):
        assert threading.get_ident() != owner_thread
        document_reads.append(path)
        return original_parse(path)

    async def app_rows(*args: Any, **kwargs: Any):
        return apps

    async def callable_rows(*args: Any, **kwargs: Any):
        return skills if allowed else []

    async def accessible(*args: Any, **kwargs: Any):
        return apps if allowed else []

    async def empty(*args: Any, **kwargs: Any):
        return []

    async def source_index_async():
        return await asyncio.to_thread(source_index)

    monkeypatch.setattr(library, "load_library_bundle_index_async", source_index_async)
    monkeypatch.setattr(service, "_resolve_bundle_dir_async", resolve)
    monkeypatch.setattr(skill_format, "parse_skill_document", parse)
    monkeypatch.setattr(service, "get_callable_skills", callable_rows)
    monkeypatch.setattr(service.App, "find", app_rows)
    monkeypatch.setattr(service.Skill, "find", empty)
    monkeypatch.setattr(
        "app.services.agent_scope.accessible_apps_for_scope", accessible
    )

    rows = await service.list_workspace_skills("workspace", user_id="reader")
    assert len(rows) == 20
    assert source_reads == [True]
    assert sorted(path_reads) == ["app-0", "app-1"]
    assert len(document_reads) == len(set(document_reads)) == 20
    assert rows[0]["description"] == "Saved description"
    assert rows[1]["description"] == "Disk description 1"
    assert all(row["tools_required"] == ["integral_list_apps"] for row in rows)

    # Source reuse must not preserve an earlier caller's visibility decision.
    allowed = False
    assert await service.list_workspace_skills("workspace", user_id="reader") == []
    assert source_reads == [True]


def test_rejected_source_does_not_fall_back_to_another_bundle(monkeypatch):
    """Keep rejected bundle files outside the discovery surface."""

    def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("A rejected bundle must not read other source files")

    monkeypatch.setattr("app.services.skill_format.parse_skill_document", forbidden)
    assert service._read_skill_disk_metadata(SimpleNamespace(key="inspect"), None) == (
        "",
        [],
    )


@pytest.mark.asyncio
async def test_tool_catalogue_build_runs_off_api_loop(monkeypatch):
    """Build editor metadata without blocking other API requests."""
    owner_thread = threading.get_ident()

    def catalogue():
        assert threading.get_ident() != owner_thread
        return [{"name": "inspect", "input_schema": {"properties": {"id": {}}}}]

    monkeypatch.setattr("app.agentive.tooling.build_tool_catalogue", catalogue)
    rows = await service.build_tool_catalogue_for_editor()
    assert rows[0]["name"] == "inspect"
    assert rows[0]["param_summary"] == "id"


@pytest.mark.asyncio
async def test_skill_list_reads_core_files_off_api_loop(monkeypatch):
    """Read the built-in list away from the request loop."""
    from app.agentive.api import agent_skills as api

    owner_thread = threading.get_ident()

    def core():
        assert threading.get_ident() != owner_thread
        return [{"id": "core:inspect", "source": "core", "key": "inspect"}]

    async def workspace(*args: Any, **kwargs: Any):
        return "workspace"

    async def empty(*args: Any, **kwargs: Any):
        return []

    monkeypatch.setattr(api, "_require_user", lambda request: "reader")
    monkeypatch.setattr(api, "_require_workspace", workspace)
    monkeypatch.setattr(api, "list_core_skills", core)
    monkeypatch.setattr(api, "list_workspace_skills", empty)
    result = await api.list_skills(SimpleNamespace())
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_effective_context_builds_catalogue_off_api_loop(monkeypatch):
    """Keep effective-context catalogue processing off the request loop."""
    from app.agentive.api import agent_skills as api

    owner_thread = threading.get_ident()

    def catalogue():
        assert threading.get_ident() != owner_thread
        return [{"name": "inspect", "description": "Inspect a record"}]

    async def workspace(*args: Any, **kwargs: Any):
        return "workspace"

    async def profile(*args: Any, **kwargs: Any):
        return SimpleNamespace(apps=[], overlay_skill_docs=[])

    async def empty(*args: Any, **kwargs: Any):
        return []

    monkeypatch.setattr(api, "_require_user", lambda request: "reader")
    monkeypatch.setattr(api, "_require_workspace", workspace)
    monkeypatch.setattr(api, "compose_workspace_agent_profile", profile)
    monkeypatch.setattr(api, "list_core_skills", lambda: [])
    monkeypatch.setattr(api, "list_workspace_skills", empty)
    monkeypatch.setattr(api, "build_tool_catalogue", catalogue)
    monkeypatch.setattr(
        "app.services.hooks.registry.get_workspace_tools", lambda workspace_id: {}
    )
    result = await api.effective_skill_context(SimpleNamespace())
    assert result["tools"][0]["name"] == "inspect"


@pytest.mark.asyncio
async def test_reset_defaults_use_configured_validated_sources_off_loop(monkeypatch):
    """Restore defaults from configured sources without synchronous compilation."""
    owner_thread = threading.get_ident()
    manifest = {
        "app": {
            "skills": [
                {"key": "inspect", "name": "Inspect", "description": "Read records"}
            ]
        }
    }

    def source_index():
        assert threading.get_ident() != owner_thread
        return "revision", {"external": SimpleNamespace(manifest=manifest)}

    def compile_manifest(*, manifest, scope_hint):
        assert threading.get_ident() != owner_thread
        assert scope_hint == "app"
        return manifest

    async def source_index_async():
        return await asyncio.to_thread(source_index)

    monkeypatch.setattr(
        "app.services.operational_model_library_sync.load_library_bundle_index_async",
        source_index_async,
    )
    monkeypatch.setattr(
        "app.services.operational_model_compile.compile_canonical_manifest",
        compile_manifest,
    )
    app = SimpleNamespace(source_operational_model_slug="external")
    assert await service._manifest_skill_defaults(app, "inspect") == (
        "Inspect",
        "Read records",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_cached_specs", [False, True])
async def test_rescan_offloads_sources_but_keeps_graph_work_on_request(
    monkeypatch, use_cached_specs
):
    """Offload catalogue compilation without moving transactional graph work."""
    import asyncio

    from app.services import operational_model_library_sync as library

    owner_thread = threading.get_ident()
    owner_task = asyncio.current_task()
    stages = []

    def load():
        assert threading.get_ident() != owner_thread
        stages.append("source")
        return [
            SimpleNamespace(slug="sample", bundle_fingerprint="fp", version="1")
        ], []

    def assert_request_task():
        assert threading.get_ident() == owner_thread
        assert asyncio.current_task() is owner_task

    async def find(*args: Any, **kwargs: Any):
        assert_request_task()
        stages.append("read")
        return []

    async def connect(*args: Any, **kwargs: Any):
        assert_request_task()
        stages.append("edge")

    async def upsert(**kwargs: Any):
        assert_request_task()
        stages.append("write")
        await kwargs["ensure_catalog_edge"](None, None)

    async def reconcile(**kwargs: Any):
        assert_request_task()
        return []

    loader_name = (
        "load_library_operational_models_with_issues_cached"
        if use_cached_specs
        else "load_library_operational_models_with_issues"
    )
    monkeypatch.setattr(library, loader_name, load)
    monkeypatch.setattr(library.OperationalModel, "find", find)
    monkeypatch.setattr(library, "upsert_seeded_library_packages", upsert)
    monkeypatch.setattr(library, "reconcile_removed_seeded_packages", reconcile)
    result = await library.sync_library_catalog_from_disk(
        catalog_registry=object(),
        ensure_catalog_edge=connect,
        use_cached_specs=use_cached_specs,
    )
    assert stages == ["source", "read", "write", "edge"]
    assert result["added"] == ["sample"]


def test_forced_rescan_serializes_with_cold_profile_validation(monkeypatch):
    """Prevent simultaneous catalogue compilers across both request paths."""
    from app.services import operational_model_library_sync as library

    barrier = threading.Barrier(2, timeout=10)
    counter_lock = threading.Lock()
    active = maximum = calls = 0

    def load():
        nonlocal active, maximum, calls
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
            calls += 1
        try:
            time.sleep(0.03)
            return [], []
        finally:
            with counter_lock:
                active -= 1

    def request(forced):
        barrier.wait()
        if forced:
            return library._load_library_specs_for_sync(use_cached_specs=False)
        return library.load_library_bundle_index()

    monkeypatch.setattr(library, "_library_source_key", lambda: ("stable",))
    monkeypatch.setattr(library, "load_library_operational_models_with_issues", load)
    library.reset_library_operational_models_cache_for_testing()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(request, [False, True]))
        assert calls == 2
        assert maximum == 1
    finally:
        library.reset_library_operational_models_cache_for_testing()
