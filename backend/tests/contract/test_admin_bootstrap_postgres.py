"""Independent first-start workers must retain one stable account identity."""

import asyncio
import json
import os
import sys
import textwrap
from typing import Any

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserLogin
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app import bootstrap_admin
from app.config import settings
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, OWNS
from app.models.nodes import (
    USERS_REGISTRY_ID,
    WORKSPACES_REGISTRY_ID,
    Entry,
    Track,
    User,
    Workspace,
)
from app.services import app_graph, authentication, personal_workspace
from app.services.app_graph import (
    catalog_track,
    catalog_user,
    ensure_integral_app_graph,
)
from app.services.authentication import (
    AuthenticationStateConflict,
    account_creation_scope,
    get_authentication_service,
)
from app.services.permissions import can_view_entry, get_user_node

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]

_WORKER = textwrap.dedent(
    """
    import asyncio,json,sys
    from jvspatial.api.auth.service import AuthenticationService
    from jvspatial.core.context import GraphContext,scoped_default_context_async
    from jvspatial.db import get_database_manager
    from jvspatial.db.factory import create_database
    from app.bootstrap_admin import _bootstrap_admin_once
    from app.services.authentication import IntegralAuthenticationService,get_authentication_service
    from jvspatial.api.auth.models import UserCreate,UserCreateAdmin

    operation=sys.argv[2]
    async def empty():
        print('EMPTY',flush=True)
        if sys.argv[1]=='first':
            assert await asyncio.to_thread(sys.stdin.readline)=='GO\\n'

    original=AuthenticationService._user_count
    async def observed(self):
        count=await original(self)
        if count==0:
            await empty()
        return count
    AuthenticationService._user_count=observed
    if operation!='bootstrap':
        lookup=IntegralAuthenticationService._find_user_by_email
        async def observed_lookup(self,email):
            value=await lookup(self,email)
            if value is None:
                await empty()
            return value
        IntegralAuthenticationService._find_user_by_email=observed_lookup

    async def run():
        db=create_database(db_type='postgres',min_size=1,max_size=1)
        get_database_manager().set_prime_database(db)
        try:
            async with scoped_default_context_async(GraphContext(database=db)):
                if operation=='bootstrap':
                    await _bootstrap_admin_once()
                else:
                    import os
                    fields={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}
                    service=get_authentication_service()
                    try:
                        if operation=='register':
                            await service.register_user(UserCreate(**fields))
                        else:
                            await service.create_user_with_roles(UserCreateAdmin(**fields,roles=['admin']))
                    except ValueError as exc:
                        assert 'already exists' in str(exc)
                        print('EXISTS',flush=True)
                        return
                print('DONE',flush=True)
        finally:
            await db.close()
    asyncio.run(run())
    """
)


async def _run_first_start_workers(postgres_raw_db, same_email, operation="bootstrap"):
    await ensure_integral_app_graph(include_library=False)
    processes = []

    async def start(label, email):
        environment = {
            **os.environ,
            "ADMIN_EMAIL": email,
            "ADMIN_PASSWORD": "disposable-bootstrap-password",
            "ADMIN_NAME": "Bootstrap fixture",
            "REGISTRATION_OPEN": "true",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _WORKER,
            label,
            operation,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    async def line(process):
        return await asyncio.wait_for(process.stdout.readline(), timeout=30)

    try:
        first = await start("first", "bootstrap@example.com")
        assert await line(first) == b"EMPTY\n"
        second = await start(
            "second", "bootstrap@example.com" if same_email else "other@example.com"
        )
        # The first worker holds after its empty-account read. Without the
        # shared fence, the second can finish creating an independent account.
        try:
            second_line = await asyncio.wait_for(second.stdout.readline(), timeout=2)
        except asyncio.TimeoutError:
            second_line = None
        if second_line == b"EMPTY\n":
            assert await line(second) == b"DONE\n"
        first.stdin.write(b"GO\n")
        await first.stdin.drain()
        assert await line(first) == b"DONE\n"
        if second_line is None:
            assert await line(second) in (b"DONE\n", b"EXISTS\n")
        else:
            assert second_line in (b"EMPTY\n", b"DONE\n")
        for process in processes:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            assert process.returncode == 0, stderr.decode()[-3000:]
        models = (
            (
                ("object", AuthUser),
                ("node", User),
                ("node", Workspace),
            )
            if operation == "bootstrap"
            else (("object", AuthUser),)
        )
        for collection, model in models:
            rows = await postgres_raw_db.find(
                collection, {"entity": model._entity_name()}
            )
            assert len(rows) == 1, (model.__name__, [row["id"] for row in rows])
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.parametrize("same_email", [True, False])
async def test_independent_first_start_workers_have_one_admin(
    postgres_raw_db, same_email
):
    """Serialize real processes before either can select an initial admin."""
    await _run_first_start_workers(postgres_raw_db, same_email)


@pytest.mark.parametrize("operation", ["register", "roles"])
async def test_independent_account_creators_cannot_duplicate_email(
    postgres_raw_db, operation
):
    """Protect both ordinary registration and privileged account creation."""
    await _run_first_start_workers(postgres_raw_db, True, operation)


async def test_duplicate_bootstrap_email_refuses_without_changing_records(
    postgres_raw_db, monkeypatch
):
    """Refuse ambiguous identities without selecting or changing either one."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "duplicate@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    for suffix in ("first", "second"):
        await AuthUser.create(
            id="o.User.bootstrap-" + suffix,
            email="duplicate@example.com",
            password_hash="retained-credential-" + suffix,
            roles=["admin"],
        )
    before = await postgres_raw_db.find("object", {"entity": AuthUser._entity_name()})
    with pytest.raises(AuthenticationStateConflict, match="[Aa]mbiguous"):
        await bootstrap_admin._bootstrap_admin_once()
    with pytest.raises(
        AuthenticationStateConflict, match="^Invalid email or password$"
    ):
        await get_authentication_service().login_user(
            UserLogin(
                email="duplicate@example.com", password="disposable-bootstrap-password"
            )
        )
    after = await postgres_raw_db.find("object", {"entity": AuthUser._entity_name()})
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    assert not await postgres_raw_db.find("node", {"entity": User._entity_name()})


@pytest.mark.parametrize("failed_step", ["profile", "workspace_catalog"])
async def test_profile_failure_rolls_back_account_and_can_retry(
    postgres_raw_db, monkeypatch, failed_step
):
    """Roll back the complete identity and rooted graph before retrying."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "atomic@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    original = bootstrap_admin._ensure_graph_profile
    original_catalog = app_graph.catalog_workspace

    async def unavailable(*args: Any):
        await original(*args)
        raise RuntimeError("profile interrupted")

    async def unavailable_catalog(*args: Any):
        raise RuntimeError("workspace interrupted")

    if failed_step == "profile":
        monkeypatch.setattr(bootstrap_admin, "_ensure_graph_profile", unavailable)
    else:
        monkeypatch.setattr(app_graph, "catalog_workspace", unavailable_catalog)
        monkeypatch.setattr(
            personal_workspace, "catalog_workspace", unavailable_catalog
        )
    with pytest.raises(RuntimeError, match="interrupted"):
        await bootstrap_admin._bootstrap_admin_once()
    for collection, model in (
        ("object", AuthUser),
        ("node", User),
        ("node", Workspace),
    ):
        assert not await postgres_raw_db.find(
            collection, {"entity": model._entity_name()}
        )
    monkeypatch.setattr(bootstrap_admin, "_ensure_graph_profile", original)
    monkeypatch.setattr(app_graph, "catalog_workspace", original_catalog)
    monkeypatch.setattr(personal_workspace, "catalog_workspace", original_catalog)
    await bootstrap_admin._bootstrap_admin_once()
    assert await get_authentication_service()._find_user_by_email("atomic@example.com")


async def test_restart_login_keeps_principal_and_owned_record_access(
    postgres_raw_db, monkeypatch
):
    """Retain the login principal and readable record in a fresh serving context."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "stable@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    await bootstrap_admin._bootstrap_admin_once()
    login = UserLogin(
        email="stable@example.com", password="disposable-bootstrap-password"
    )
    initial = await get_authentication_service().login_user(login)
    profile = await get_user_node(initial.user.id)
    workspace = await personal_workspace.ensure_personal_workspace(profile)
    track = await Track.create(title="Retained", workspace_id=workspace.id)
    await catalog_track(track)
    await profile.connect(track, edge=OWNS)
    entry = await Entry.create(title="Retained record", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    retained = await postgres_raw_db.find_one("node", {"id": entry.id})
    other = create_database(
        db_type="postgres",
        dsn=os.environ["JVSPATIAL_POSTGRES_DSN"],
        min_size=1,
        max_size=1,
    )
    try:
        async with scoped_default_context_async(GraphContext(database=other)):
            await bootstrap_admin._bootstrap_admin_once()
            restarted = await get_authentication_service().login_user(login)
            assert restarted.user.id == initial.user.id
            assert (await get_user_node(restarted.user.id)).id == profile.id
            assert await can_view_entry(restarted.user.id, entry.id)
            assert await other.find_one("node", {"id": entry.id}) == retained
    finally:
        await other.close()


async def test_invalid_or_inactive_account_is_not_replaced_or_reactivated(
    postgres_raw_db, monkeypatch
):
    """Keep uncertain lifecycle facts closed instead of minting another identity."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "inactive@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    identity = await AuthUser.create(
        email="inactive@example.com", password_hash="retained", is_active=False
    )
    before = await postgres_raw_db.find_one("object", {"id": identity.id})
    with pytest.raises(AuthenticationStateConflict, match="inactive"):
        await bootstrap_admin._bootstrap_admin_once()
    assert await postgres_raw_db.find_one("object", {"id": identity.id}) == before
    await postgres_raw_db.find_one_and_update(
        "object", {"id": identity.id}, {"$unset": {"context.is_active": ""}}
    )
    invalid = await postgres_raw_db.find_one("object", {"id": identity.id})
    with pytest.raises(AuthorizationUnavailableError):
        await bootstrap_admin._bootstrap_admin_once()
    assert await postgres_raw_db.find_one("object", {"id": identity.id}) == invalid
    assert (
        await postgres_raw_db.count("object", {"entity": AuthUser._entity_name()}) == 1
    )


async def test_duplicate_profiles_require_recovery_without_relinking(
    postgres_raw_db, monkeypatch
):
    """Preserve ambiguous profiles and require a separate recovery decision."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "profiles@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    identity = await AuthUser.create(
        email="profiles@example.com", password_hash="retained", roles=["admin"]
    )
    for _ in range(2):
        profile = await User.create(user_id=identity.id)
        await catalog_user(profile)
    before = await postgres_raw_db.find("node", {"entity": User._entity_name()})
    with pytest.raises(AuthenticationStateConflict, match="graph profile"):
        await bootstrap_admin._bootstrap_admin_once()
    assert await postgres_raw_db.find("node", {"entity": User._entity_name()}) == before
    assert not await postgres_raw_db.find("node", {"entity": Workspace._entity_name()})


async def test_reviewed_duplicate_quarantine_preserves_both_principals_graphs(
    postgres_raw_db, monkeypatch
):
    """Model an explicit offline operator choice, never automatic deduplication."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "recovery@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    await bootstrap_admin._bootstrap_admin_once()
    service = get_authentication_service()
    login = UserLogin(
        email="recovery@example.com", password="disposable-bootstrap-password"
    )
    session = await service.login_user(login)
    owner = await get_user_node(session.user.id)
    workspace = await personal_workspace.ensure_personal_workspace(owner)
    track = await Track.create(title="Retained recovery", workspace_id=workspace.id)
    await catalog_track(track)
    await owner.connect(track, edge=OWNS)
    entry = await Entry.create(title="Owner's record", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    # The other identity has its own graph and credentials. Preserve all of it.
    duplicate = await AuthUser.create(
        email=login.email, password_hash="other-retained-credential", roles=["admin"]
    )
    other_profile = await User.create(user_id=duplicate.id, display_name="Other")
    await catalog_user(other_profile)
    await personal_workspace.ensure_personal_workspace(other_profile)
    stale_token, _ = service._generate_jwt_token(duplicate.id, duplicate.email)
    graph_before = {
        collection: await postgres_raw_db.find(collection, {})
        for collection in ("node", "edge")
    }
    with pytest.raises(
        AuthenticationStateConflict, match="^Invalid email or password$"
    ):
        await service.login_user(login)
    # Only this synthetic fixture's operator selects the known record owner.
    # Production recovery requires a reviewed identity choice and cold backup.
    async with account_creation_scope(postgres_raw_db):
        recovery = get_authentication_service()
        current = await recovery.context.get(AuthUser, duplicate.id)
        current.email = "quarantined-bootstrap@example.invalid"
        current.is_active = False
        await recovery.context.save(current)
    await bootstrap_admin._bootstrap_admin_once()
    restored = await get_authentication_service().login_user(login)
    assert restored.user.id == session.user.id
    assert await can_view_entry(restored.user.id, entry.id)
    assert await get_authentication_service().validate_token(stale_token) is None
    retained = await postgres_raw_db.find_one("object", {"id": duplicate.id})
    assert retained["context"]["password_hash"] == "other-retained-credential"
    assert retained["context"]["roles"] == ["admin"]
    assert retained["context"]["is_active"] is False
    for collection, before in graph_before.items():
        assert await postgres_raw_db.find(collection, {}) == before


async def test_blocked_account_creator_times_out_without_partial_identity(
    postgres_raw_db, monkeypatch
):
    """Bound contention and allow a clean retry after the owning lock releases."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "bounded@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    monkeypatch.setattr(authentication, "_ACCOUNT_CREATION_LOCK_TIMEOUT_SECONDS", 0.1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with account_creation_scope(postgres_raw_db):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(AuthorizationUnavailableError):
            await bootstrap_admin._bootstrap_admin_once()
        assert not await postgres_raw_db.find(
            "object", {"entity": AuthUser._entity_name()}
        )
    finally:
        release.set()
        await holder
    await bootstrap_admin._bootstrap_admin_once()
    assert await get_authentication_service()._find_user_by_email("bounded@example.com")


@pytest.mark.parametrize("registry_id", [USERS_REGISTRY_ID, WORKSPACES_REGISTRY_ID])
async def test_missing_graph_registry_refuses_without_partial_identity(
    postgres_raw_db, monkeypatch, registry_id
):
    """Refuse an incomplete graph shell before creating an unrooted identity."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "unrooted@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    # Deliberate corruption in this disposable fixture models failed graph init.
    await postgres_raw_db.delete("node", registry_id)
    before = {
        collection: await postgres_raw_db.find(collection, {})
        for collection in ("node", "edge", "object")
    }
    with pytest.raises(RuntimeError, match="graph registries are unavailable"):
        await bootstrap_admin._bootstrap_admin_once()
    for collection, rows in before.items():
        assert await postgres_raw_db.find(collection, {}) == rows


async def test_malformed_profile_is_not_replaced_with_another_identity(
    postgres_raw_db, monkeypatch
):
    """Count primary profile rows even when model hydration would skip one."""
    await ensure_integral_app_graph(include_library=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "malformed-profile@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "disposable-bootstrap-password")
    identity = await AuthUser.create(
        email="malformed-profile@example.com", password_hash="retained", roles=["admin"]
    )
    profile = await User.create(user_id=identity.id)
    await catalog_user(profile)
    await postgres_raw_db.find_one_and_update(
        "node",
        {"id": profile.id},
        {"$set": {"context.display_name": {"invalid": True}}},
    )
    before = await postgres_raw_db.find("node", {"entity": User._entity_name()})
    with pytest.raises(
        AuthenticationStateConflict, match="Invalid bootstrap graph profile"
    ):
        await bootstrap_admin._bootstrap_admin_once()
    assert await postgres_raw_db.find("node", {"entity": User._entity_name()}) == before
