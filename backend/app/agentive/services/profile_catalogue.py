"""Project canonical AI Skills and registered reads into managed profiles.

This slice exposes declarative instructions and registered read queries. It
does not mount a generic broker or infer public access from an installation.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.agentive.services.agent_skills import list_core_skills
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.workspace_agent_profile import compose_workspace_agent_profile
from app.api.errors import JVSpatialAPIException
from app.contracts.runtime import ExecutionScope
from app.models.nodes import App
from app.schemas.policy import Resource, Subject
from app.services.app_queries.dispatch import (
    invoke_app_query,
    list_app_queries,
    policy_evaluate,
)
from app.services.workspace_permissions import can_access_workspace


@dataclass(frozen=True)
class InstalledCapability:
    declaration: dict[str, Any]
    invoke: Callable[[dict], Awaitable[Any]]


def _key(kind, app_id, declaration_key):
    identity = hashlib.sha256(f"{app_id}\0{declaration_key}".encode()).hexdigest()
    return f"{kind}_{identity[:48]}"


def _query(app, query, workspace, principal):
    async def invoke(arguments):
        result = await invoke_app_query(
            user_id=principal,
            workspace_id=workspace,
            app_id=app.app_id,
            query_key=query["key"],
            params=arguments,
        )
        return result["output"]

    return InstalledCapability(
        {
            "kind": "tool",
            "source": "app",
            "app_id": app.app_id,
            "app_name": app.name,
            "label": f"{app.name} · {query['name'] or query['key']}",
            "description": f"{query['name'] or query['key']} ({app.name}). {query['description']}",
            "input_schema": query["input_schema"],
        },
        invoke,
    )


def _skill(label, description, body, *, source, skill_id=None, app=None):
    async def invoke(arguments):
        return {"instructions": body}

    return InstalledCapability(
        {
            "kind": "skill",
            "source": source,
            "label": label,
            "description": description,
            **({"skill_id": skill_id} if skill_id else {}),
            **({"app_id": app.app_id, "app_name": app.name} if app else {}),
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        invoke,
    )


async def installed_catalogue(workspace, principal, audience):
    if audience != "staff" or await can_access_workspace(principal, workspace) not in (
        "owner",
        "admin",
        "member",
    ):
        return {}
    profile = await compose_workspace_agent_profile(workspace, user_id=principal)
    result = {
        _key("skill", "core", row["id"]): _skill(
            row["name"],
            row["description"],
            row["resolved_body"],
            source="core",
            skill_id=row["id"],
        )
        for row in await asyncio.to_thread(list_core_skills)
        if row["resolved_body"]
    }
    live_apps = {}
    for app in profile.apps:
        # Recheck live lifecycle. In particular the ordinary list endpoint
        # also admits awaiting-settings Apps, which profiles never mount.
        current = await App.get(app.app_id)
        if (
            current is None
            or current.workspace_id != workspace
            or current.lifecycle_state != "active"
        ):
            continue
        try:
            listed = await list_app_queries(
                user_id=principal, workspace_id=workspace, app_id=app.app_id
            )
        except JVSpatialAPIException as exc:
            if exc.status_code not in {400, 403, 404}:
                raise
            continue
        live_apps[app.app_id] = app
        scope = ExecutionScope.create(
            principal_id=principal, workspace_id=workspace, origin="app_query"
        )
        resource = Resource(kind="app", id=app.app_id, scope=f"app:{app.app_id}")
        for query in listed["queries"]:
            decision = await policy_evaluate(
                subject=Subject(kind="human", id=principal),
                action=str(query["policy_action"] or "app.read").strip(),
                resource=resource,
                execution_scope=scope,
            )
            if decision.allowed:
                result[_key("query", app.app_id, query["key"])] = _query(
                    app, query, workspace, principal
                )
    for doc in profile.overlay_skill_docs:
        if (
            doc.metadata.get("out_of_focus")
            or doc.metadata.get("kind") == "connected_sources"
        ):
            continue
        app_id = doc.metadata.get("app_id")
        app = live_apps.get(app_id)
        if app_id and app is None:
            continue
        skill_id = doc.metadata.get("skill_id")
        if doc.metadata.get("origin") == "workspace" and skill_id:
            result[_key("skill", "workspace", skill_id)] = _skill(
                doc.metadata["skill_name"],
                doc.description,
                doc.body,
                source="workspace",
                skill_id=skill_id,
                app=app,
            )
        elif app is not None:
            result[_key("skill", app.app_id, doc.name)] = _skill(
                f"{app.name} · {doc.metadata.get('skill_name') or doc.name}",
                doc.description,
                doc.body,
                source="app",
                skill_id=skill_id,
                app=app,
            )
    return result


async def invoke_installed(workspace, principal, audience, name, arguments):
    current = (await installed_catalogue(workspace, principal, audience)).get(name)
    if current is None:
        raise ProfileDenied("Installed capability unavailable")
    try:
        return await current.invoke(dict(arguments))
    except JVSpatialAPIException as exc:
        if exc.status_code not in {403, 404}:
            raise
        raise ProfileDenied("Installed capability unavailable") from exc
