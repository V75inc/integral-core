"""Server-owned draft/test/publish/bind lifecycle for connection profiles."""

from base64 import b64decode, b64encode
from copy import deepcopy
from typing import NamedTuple
from uuid import uuid4

from jvspatial.api.exceptions import InsufficientPermissionsError
from jvspatial.core.context import get_default_context
from starlette.concurrency import run_in_threadpool

from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.profile_catalogue import (
    installed_catalogue,
    invoke_installed,
)
from app.agentive.services.profile_host import profile_host
from app.api.errors import BadRequestError
from app.models.edges import HAS_CONNECTION_PROFILES
from app.models.nodes import Workspace, WorkspaceConnectionProfiles
from app.schemas.agentive.connection_profiles import (
    PROFILE_AVATAR_MAX_BYTES,
    PublicWebChatRoute,
)
from app.schemas.avatar import AvatarValidationError
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.avatar_resize import resize_avatar
from app.services.workspace_permissions import is_workspace_admin_or_owner


async def require_profile_manager(actor, workspace):
    """Require workspace owner or administrator access."""
    if (
        not actor
        or not workspace
        or not await is_workspace_admin_or_owner(actor, workspace)
    ):
        raise InsufficientPermissionsError("Workspace admin or owner required")


async def record(workspace, *, create=False):
    """Resolve the workspace profile node and verify its ownership edge."""
    db = get_default_context().database
    if create:
        locked = await db.find_one_and_update(
            "node",
            {"id": workspace, "entity": "Workspace"},
            {"$inc": {"context.storage_bytes_used": 0}},
        )
        if locked is None:
            raise BadRequestError("Workspace unavailable")
    parent = await Workspace.find_one({"id": workspace})
    if parent is None:
        raise BadRequestError("Workspace unavailable")
    identity = f"n.WorkspaceConnectionProfiles.{workspace}"
    found = await WorkspaceConnectionProfiles.find_one({"id": identity})
    if found is None and create:
        found = await WorkspaceConnectionProfiles.create(
            id=identity, workspace_id=workspace
        )
        await parent.connect(found, edge=HAS_CONNECTION_PROFILES)
    if found:
        edges = await get_default_context().find_edges_between(
            workspace, found.id, edge_class=HAS_CONNECTION_PROFILES
        )
        if found.workspace_id != workspace or len(edges) != 1:
            raise BadRequestError("Profile configuration needs repair")
    return found


WORKSPACE_ASSISTANT_KEY = "workspace-assistant"
AVATAR_PREFIXES = {
    "data:image/png;base64",
    "data:image/jpeg;base64",
    "data:image/webp;base64",
}


def normalize_avatar(value):
    """Validate and normalize an avatar to a PNG data URL."""
    header, _, encoded = value.partition(",")
    try:
        if header not in AVATAR_PREFIXES:
            raise ValueError("Unsupported image format")
        content = b64decode(encoded, validate=True)
        if len(content) > PROFILE_AVATAR_MAX_BYTES:
            raise ValueError("Image is too large")
        png = resize_avatar(content, sizes=(64,), max_pixels=4_000_000)[64]
    except (ValueError, AvatarValidationError):
        raise BadRequestError(
            "Use a valid PNG, JPEG or WebP avatar up to 512 KB and 4 megapixels."
        ) from None
    return "data:image/png;base64," + b64encode(png).decode("ascii")


async def update_avatar(target, previous, body):
    """Apply an explicit avatar edit or preserve the existing avatar."""
    value = body.avatar if "avatar" in body.model_fields_set else previous.get("avatar")
    target.pop("avatar", None)
    if value is not None:
        target["avatar"] = (
            value
            if value == previous.get("avatar")
            else await run_in_threadpool(normalize_avatar, value)
        )


def workspace_assistant_identity(profiles):
    """Project the default assistant identity for display."""
    saved = profiles.get(WORKSPACE_ASSISTANT_KEY, {})
    return {
        "key": WORKSPACE_ASSISTANT_KEY,
        "name": saved.get("name") or "Integral AI",
        "description": "Your workspace assistant",
        "icon": saved.get("icon") or "integral",
        **({"avatar": saved["avatar"]} if saved.get("avatar") else {}),
    }


class VoiceProfileIdentity(NamedTuple):
    name: str
    persona: str


async def workspace_voice_profile_identity(workspace) -> VoiceProfileIdentity:
    """Read from one saved snapshot in an already-authorized workspace."""
    found = await record(workspace)
    profiles = found.profiles if found else {}
    saved = profiles.get(WORKSPACE_ASSISTANT_KEY, {})
    return VoiceProfileIdentity(
        workspace_assistant_identity(profiles)["name"], saved.get("persona") or ""
    )


async def workspace_assistant_persona(workspace):
    """Read the default workspace assistant persona."""
    return (await workspace_voice_profile_identity(workspace)).persona


def starter(key):
    """Build the initial draft for a built-in profile."""
    return {
        "key": key,
        "audience": key,
        "name": f"{key.title()} assistant",
        "description": "",
        "icon": "integral",
        "persona": "",
        "tools": [],
        "omissions": [],
    }


def validate_draft(draft, ceiling):
    """Check selected tools and omissions against the permitted catalogue."""
    if (
        len(set(draft["tools"])) != len(draft["tools"])
        or not set(draft["tools"]) <= ceiling.keys()
        or not set(draft["omissions"]) <= ceiling.keys()
    ):
        raise BadRequestError("Capabilities must remain within the host ceiling")


async def catalogue(actor, workspace, key):
    """Project capability declarations visible to the principal."""
    installed = await installed_catalogue(workspace, actor, key)
    return {name: cap.declaration for name, cap in installed.items()}


def audience_of(draft):
    # Existing persisted starter drafts predate the explicit audience field.
    """Read the explicit or original built-in profile audience."""
    return draft.get("audience", draft["key"])


def eligible_public_web_chat_profile(profile):
    """First hosted channel admits public profiles with no tool capabilities."""
    return bool(
        profile
        and profile.get("audience", profile.get("key")) == "public"
        and profile.get("tools") == []
    )


async def describe(actor, workspace):
    """Return guarded workspace settings for this surface."""
    await require_profile_manager(actor, workspace)
    found = await record(workspace)
    profiles = found.profiles if found else {}
    try:
        connections = await profile_host().connections(workspace)
    except PermissionError:
        connections = []
    return {
        "revision": found.revision if found else 0,
        "profiles": {
            **{key: {"draft": starter(key)} for key in ("public", "staff")},
            **{
                key: item
                for key, item in profiles.items()
                if key != WORKSPACE_ASSISTANT_KEY
            },
        },
        "workspace_assistant": {
            **workspace_assistant_identity(profiles),
            "persona": profiles.get(WORKSPACE_ASSISTANT_KEY, {}).get("persona") or "",
        },
        "native_chat_profiles": found.native_chat_profiles if found else [],
        "catalogue": {
            key: await catalogue(actor, workspace, key) for key in ["public", "staff"]
        },
        "connections": connections,
        "bindings": found.bindings if found else {},
    }


async def mutate_config(actor, workspace, revision, action):
    """Update profile configuration at the expected workspace revision."""
    async with postgres_graph_transaction():
        await require_profile_manager(actor, workspace)
        found = await record(workspace, create=True)
        if found.revision != revision:
            raise BadRequestError("Profiles changed. Reload and try again.")
        profiles, bindings = deepcopy(found.profiles), deepcopy(found.bindings)
        native = list(found.native_chat_profiles)
        public_web_chat = PublicWebChatRoute.model_validate(found.public_web_chat)
        original_route = public_web_chat.model_dump()
        await action(profiles, bindings, native, public_web_chat)
        values = {
            "context.profiles": profiles,
            "context.bindings": bindings,
            "context.native_chat_profiles": native,
            "context.revision": found.revision + 1,
        }
        route = public_web_chat.model_dump()
        if route != original_route:
            values["context.public_web_chat"] = route
        updated = await get_default_context().database.find_one_and_update(
            "node",
            {"id": found.id, "context.revision": found.revision},
            {"$set": values},
        )
        if updated is None:
            raise BadRequestError("Profiles changed. Reload and try again.")
    return await describe(actor, workspace)


async def mutate(actor, workspace, body, action):
    """Apply a single-profile change through the configuration revision guard."""

    async def update(profiles, bindings, native, public_web_chat):
        if body.key in ("public", "staff"):
            profiles.setdefault(body.key, {"draft": starter(body.key)})
        if body.key not in profiles:
            raise BadRequestError("Profile unavailable")
        await action(profiles[body.key], bindings, native, public_web_chat)

    return await mutate_config(actor, workspace, body.expected_revision, update)


async def rename_workspace_assistant(actor, workspace, body):
    """Update the default workspace assistant identity."""

    async def rename(profiles, bindings, native, _public_web_chat):
        name = body.name.strip()
        if not name:
            raise BadRequestError("Enter an assistant name")
        previous = profiles.get(WORKSPACE_ASSISTANT_KEY, {})
        appearance = {**previous, "name": name}
        if body.icon is not None:
            appearance["icon"] = body.icon
        if body.persona is not None:
            appearance["persona"] = body.persona.strip()
        await update_avatar(appearance, previous, body)
        profiles[WORKSPACE_ASSISTANT_KEY] = appearance

    return await mutate_config(actor, workspace, body.expected_revision, rename)


async def create_profile(actor, workspace, body):
    """Create a new workspace profile draft."""
    key = f"p_{uuid4().hex}"

    async def create(profiles, bindings, native, _public_web_chat):
        if len(profiles) >= 100:
            raise BadRequestError("Workspace profile limit reached")
        if not body.name.strip():
            raise BadRequestError("Enter a profile name")
        draft = body.model_dump(exclude={"expected_revision"})
        draft.update(key=key, name=body.name.strip(), audience=body.audience or "staff")
        validate_draft(draft, await catalogue(actor, workspace, draft["audience"]))
        await update_avatar(draft, {}, body)
        profiles[key] = {"draft": draft}

    result = await mutate_config(actor, workspace, body.expected_revision, create)
    return {**result, "created_profile": key}


async def set_native_chat_profiles(actor, workspace, body):
    """Set the published profiles offered in workspace chat."""

    async def update(profiles, bindings, native, _public_web_chat):
        for key in body.profiles:
            published = profiles.get(key, {}).get("published")
            if not published or audience_of(published) != "staff":
                raise BadRequestError("Native chat requires a published staff profile")
        native[:] = list(dict.fromkeys(body.profiles))

    return await mutate_config(actor, workspace, body.expected_revision, update)


async def save_draft(actor, workspace, body):
    """Save a validated draft without changing the published profile."""

    async def save(item, bindings, native, _public_web_chat):
        if body.key != body.draft.key:
            raise BadRequestError("Profile identity cannot be changed")
        draft = body.draft.model_dump()
        draft["audience"] = body.draft.audience or audience_of(item["draft"])
        validate_draft(draft, await catalogue(actor, workspace, draft["audience"]))
        await update_avatar(draft, item["draft"], body.draft)
        item["draft"] = draft
        item.pop("test", None)

    return await mutate(actor, workspace, body, save)


def fingerprint(draft, ceiling):
    """Hash the draft and selected capability declarations."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            [draft, {name: ceiling[name] for name in sorted(set(draft["tools"]))}],
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def test_draft(actor, workspace, body):
    """Run an inert test for the selected workspace draft."""

    async def test(item, bindings, native, _public_web_chat):
        from app.agentive.services.profile_testing import test_profile_draft

        draft = item["draft"]
        ceiling = await catalogue(actor, workspace, audience_of(draft))
        validate_draft(draft, ceiling)
        result = await test_profile_draft(draft, workspace, ceiling)
        item["test"] = {**result, "fingerprint": fingerprint(draft, ceiling)}

    return await mutate(actor, workspace, body, test)


async def _validate_connection_assignment(workspace, key, audience, ids, bindings):
    """Use the same host and existing-assignment checks for every management path."""
    try:
        connections = await profile_host().connections(workspace)
    except ProfileDenied:
        raise BadRequestError("No eligible external connection is registered") from None
    eligible = {
        connection["id"]
        for connection in connections
        if connection["audience"] == audience
        and connection.get("publish_assignable") is True
        and not connection.get("verification_path")
    }
    if not set(ids) <= eligible:
        raise BadRequestError(
            "A selected external connection is no longer eligible for this audience"
        )
    if any(bindings.get(connection_id) not in (None, key) for connection_id in ids):
        raise BadRequestError(
            "A selected external connection is already assigned to another profile"
        )


async def publish(actor, workspace, body):
    """Validate and publish a profile with its requested assignments."""

    async def activate(item, bindings, native, public_web_chat):
        from app.agentive.services.profile_testing import test_profile_draft

        draft = item["draft"]
        audience = audience_of(draft)
        ceiling = await catalogue(actor, workspace, audience)
        validate_draft(draft, ceiling)
        published = item.get("published")
        if (
            public_web_chat.profile_key == body.key
            and not eligible_public_web_chat_profile(draft)
        ):
            raise BadRequestError(
                "Remove the Public Web Chat assignment before publishing this audience or capabilities"
            )
        if published and audience_of(published) != audience:
            if body.key in bindings.values():
                raise BadRequestError(
                    "Resolve the existing connection before changing the published audience"
                )
            if audience == "public" and body.key in native:
                raise BadRequestError(
                    "Remove this profile from workspace chat before publishing it as public"
                )
        if body.offer_in_workspace_chat and audience != "staff":
            raise BadRequestError(
                "Only workspace profiles can be offered in workspace chat"
            )
        if body.connection_ids:
            await _validate_connection_assignment(
                workspace, body.key, audience, body.connection_ids, bindings
            )
        # Validate through the inert Harness on the exact revision being published.
        result = await test_profile_draft(draft, workspace, ceiling)
        if result["real_actions"] != 0:
            raise BadRequestError("Profile validation failed")
        item["test"] = {**result, "fingerprint": fingerprint(draft, ceiling)}
        if (
            not published
            or {key: value for key, value in published.items() if key != "version"}
            != draft
        ):
            item["published"] = {
                **deepcopy(draft),
                "version": body.expected_revision + 1,
            }
        if body.offer_in_workspace_chat and body.key not in native:
            native.append(body.key)
        bindings.update(dict.fromkeys(body.connection_ids, body.key))

    return await mutate(actor, workspace, body, activate)


async def bind(actor, workspace, body):
    """Assign an eligible external connection to a published profile."""

    async def attach(item, bindings, native, _public_web_chat):
        published = item.get("published")
        if not published:
            raise BadRequestError("Publish a tested profile before binding")
        await _validate_connection_assignment(
            workspace, body.key, audience_of(published), (body.connection_id,), bindings
        )
        bindings[body.connection_id] = body.key

    return await mutate(actor, workspace, body, attach)


def _published_staff_identity(profiles, key):
    published = profiles.get(key, {}).get("published")
    if not published or audience_of(published) != "staff":
        return None
    return {
        "key": key,
        "name": published["name"],
        "description": published.get("description") or "Your workspace assistant",
        "icon": published.get("icon") or "integral",
        **({"avatar": published["avatar"]} if published.get("avatar") else {}),
    }


async def thread_profile_identities(workspace, keys):
    """Project display metadata for keys from already authorized chat threads."""
    if not workspace or not keys:
        return {}
    found = await record(workspace)
    profiles = found.profiles if found else {}
    return {
        key: identity
        for key in keys
        if (
            identity := (
                workspace_assistant_identity(profiles)
                if key == WORKSPACE_ASSISTANT_KEY
                else _published_staff_identity(profiles, key)
            )
        )
        is not None
    }


async def native_chat_choices(workspace, principal):
    """List published staff profiles available to the workspace principal."""
    from app.services.workspace_permissions import can_access_workspace

    if not workspace or await can_access_workspace(principal, workspace) not in (
        "owner",
        "admin",
        "member",
    ):
        return []
    found = await record(workspace)
    if found is None:
        return []
    return [
        identity
        for key in found.native_chat_profiles
        if (identity := _published_staff_identity(found.profiles, key)) is not None
    ]


async def require_native_chat_profile(workspace, principal, key):
    """Require an offered chat profile for this workspace principal."""
    if not any(
        choice["key"] == key
        for choice in await native_chat_choices(workspace, principal)
    ):
        raise InsufficientPermissionsError(message="This chat profile is unavailable")


async def open_published_profile(
    workspace, connection_id, principal_id, *, native_profile_key=None
):
    """Open the current published profile and return its persona."""
    binding, identity = await open_published_profile_with_identity(
        workspace, connection_id, principal_id, native_profile_key=native_profile_key
    )
    return binding, identity.persona


async def open_published_profile_with_identity(
    workspace, connection_id, principal_id, *, native_profile_key=None
):
    """Read identity and permissions from the same authorized published revision."""
    from app.agentive.harness.connection_profile import NativeConnectionProfile
    from app.agentive.services.connection_profiles import (
        ConnectionGrant,
        ConnectionProfile,
        ConnectionProfileExecutor,
    )
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.workspace_permissions import can_access_workspace

    host = profile_host() if native_profile_key is None else None

    async def resolve(connection, principal):
        with isolated_permissions_cache():
            return await resolve_current(connection, principal)

    async def resolve_current(connection, principal):
        found = await record(workspace)
        key = found.bindings.get(connection) if found else None
        if host is None:
            if (
                connection != "native-chat"
                or not found
                or native_profile_key not in found.native_chat_profiles
                or await can_access_workspace(principal, workspace)
                not in ("owner", "admin", "member")
            ):
                return None
            key = native_profile_key
        published = found.profiles.get(key, {}).get("published") if key else None
        if not published:
            return None
        audience = audience_of(published)
        if host is None and audience != "staff":
            return None
        if host is not None and not any(
            item["id"] == connection and item["audience"] == audience
            for item in await host.connections(workspace)
        ):
            return None
        identity = (
            await host.resolve_principal(workspace, connection, principal)
            if host is not None
            else {
                "principal_id": principal,
                "audience": "staff",
                "verified_staff": True,
            }
        )
        if (
            not identity
            or identity["audience"] != audience
            or identity.get("principal_id") != principal
        ):
            return None
        return ConnectionGrant(
            connection,
            principal,
            workspace,
            f"{key}:{published['version']}",
            audience,
            verified_staff=identity.get("verified_staff", False),
        )

    grant = await resolve(connection_id, principal_id)
    if grant is None:
        raise ProfileDenied("Connection is unavailable")
    found = await record(workspace)
    key = grant.profile_key.split(":", 1)[0]
    published = found.profiles.get(key, {}).get("published") if found else None
    if not published or grant.profile_key != f"{key}:{published['version']}":
        raise ProfileDenied("Profile changed; start a new session")
    ceiling = await catalogue(principal_id, workspace, grant.audience)
    # Legacy pilot names never become installed capabilities implicitly.
    selected = set(published["tools"]) & ceiling.keys()

    async def authorize(current, name):
        with isolated_permissions_cache():
            if host is not None and not await host.authorize(current, name):
                return False
            return name in await catalogue(
                current.principal_id, current.workspace_id, current.audience
            )

    def handler(name):
        async def run(current, session, arguments):
            with isolated_permissions_cache():
                return await invoke_installed(
                    current.workspace_id,
                    current.principal_id,
                    current.audience,
                    name,
                    arguments,
                )

        return run

    declared = ConnectionProfile(
        grant.profile_key,
        grant.audience,
        frozenset(selected),
        frozenset(published["omissions"]),
        revision=str(published["version"]),
        human_only_tools=frozenset(
            name for name in selected if ceiling[name].get("human_only")
        ),
    )
    executor = ConnectionProfileExecutor(
        (declared,), resolve, authorize, {name: handler(name) for name in selected}
    )
    binding = await NativeConnectionProfile.open(
        executor,
        connection_id,
        principal_id,
        declarations={name: ceiling[name] for name in selected},
        turn_scoped=True,
    )
    return binding, VoiceProfileIdentity(
        published.get("name") or "Integral AI", published.get("persona") or ""
    )
