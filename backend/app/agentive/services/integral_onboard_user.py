"""ONBD-01 — LLM-driven onboarding state machine (A2).

Called by the agentive chat dispatcher via the ``integral_onboard_user``
MCP tool. The LLM advances the state by passing the previous step + an
optional context dict (e.g. ``selected_domain``, ``selected_spaces``,
``theme_choice``).

The tool emits a single ``user.update`` ChangeEvent on ``finalize``.

Concrete App + Track creation uses the in-process service helpers
extracted in Plan 09-05 (``services/app_service.py`` +
``services/track_service.py``) so the ROADMAP AC (>=1 App + >=1 Track)
is met by construction.

User lookup uses the canonical ``await User.get(user_id)`` pattern
(Plan 09-05 B1 — there is no alternate helper).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.nodes import User
from app.services.app_service import create_app_for_user
from app.services.change_event import emit_change_event as _emit_user_update
from app.services.operational_model_runtime import list_library_packages
from app.services.personal_workspace import ensure_personal_workspace
from app.services.track_service import create_track_in_space

_STEPS = (
    "start",
    "ask_domain",
    "propose_spaces",
    "create_tracks",
    "set_retrieval_mode",
    "set_theme",
    "finalize",
)


async def integral_onboard_user(
    *,
    user_id: str,
    step: str = "start",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Advance one step in the onboarding flow.

    Returns ``{"next_step", "prompt", "completed", …}`` for the LLM
    dispatcher to relay back to the user. The final ``finalize`` step
    sets ``User.onboarded_at`` and emits a single ``user.update``
    ChangeEvent.

    Raises ``RuntimeError`` when the user is not found.
    """
    if step not in _STEPS:
        return {
            "next_step": "start",
            "prompt": "Unknown step; restarting.",
            "completed": False,
        }

    ctx = context or {}
    user = await User.get(user_id)
    if not user:
        return {"completed": False, "error": "user_not_found"}

    if step == "start":
        return {
            "next_step": "ask_domain",
            "prompt": (
                f"Welcome to Integral, {user.display_name or 'there'}. "
                "What domain do you want to start with? (e.g., "
                "personal-knowledge, project-management, content-calendar)"
            ),
            "completed": False,
        }

    if step == "ask_domain":
        return {
            "next_step": "propose_spaces",
            "prompt": "Great. I'll propose a few starter apps from our library.",
            "completed": False,
        }

    if step == "propose_spaces":
        packages = await list_library_packages()
        # Filter / rank by ctx.get('domain') if supplied (simple substring
        # match against name/description — the LLM dispatcher can pre-rank
        # before passing a richer ``context.domain`` payload).
        domain = str(ctx.get("domain") or "").strip().casefold()
        candidates = [p for p in packages if p.scope == "app"]
        if domain:
            ranked = sorted(
                candidates,
                key=lambda p: (
                    0 if domain in (p.name or "").casefold() else 1,
                    0 if domain in (p.description or "").casefold() else 1,
                ),
            )
            candidates = ranked
        candidates = candidates[:3]
        return {
            "next_step": "create_tracks",
            "prompt": "Here are 2-3 proposed apps. Confirm or pick.",
            "completed": False,
            "suggestions": [
                {"id": p.id, "name": p.name, "description": p.description}
                for p in candidates
            ],
        }

    if step == "create_tracks":
        selected = ctx.get("selected_spaces") or []
        created = await _create_starter_spaces_and_tracks(user_id, selected)
        return {
            "next_step": "set_retrieval_mode",
            "prompt": (
                "Apps and starter tracks created. Which retrieval mode? "
                "(graph, semantic, hybrid; default: hybrid)"
            ),
            "completed": False,
            "created": created,
        }

    if step == "set_retrieval_mode":
        mode = str(ctx.get("retrieval_mode") or "hybrid")
        prefs = dict(user.preferences or {})
        prefs["retrieval_mode"] = mode
        user.preferences = prefs
        user.updated_at = datetime.now(timezone.utc).isoformat()
        await user.save()
        return {
            "next_step": "set_theme",
            "prompt": "Retrieval mode set. Theme: light, dark, or system?",
            "completed": False,
        }

    if step == "set_theme":
        theme = str(ctx.get("theme") or "system")
        prefs = dict(user.preferences or {})
        prefs["theme"] = theme
        user.preferences = prefs
        user.updated_at = datetime.now(timezone.utc).isoformat()
        await user.save()
        return {
            "next_step": "finalize",
            "prompt": "Almost done. Let me wrap up.",
            "completed": False,
        }

    # step == "finalize" — guarded by the _STEPS membership check above.
    before = {"onboarded_at": user.onboarded_at}
    now_iso = datetime.now(timezone.utc).isoformat()
    user.onboarded_at = now_iso
    user.updated_at = now_iso
    await user.save()
    # D-05 single emission path — exactly one user.update per finalize.
    await _emit_user_update(
        actor_kind="human",
        actor_id=user_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=before,
        after={"onboarded_at": user.onboarded_at},
        scope=f"user:{user_id}",
        details={"section": "onboarded_at"},
    )
    return {
        "next_step": None,
        "prompt": "Welcome aboard. You're all set.",
        "completed": True,
        "summary": {
            "onboarded_at": user.onboarded_at,
            "theme": (user.preferences or {}).get("theme"),
            "retrieval_mode": (user.preferences or {}).get("retrieval_mode"),
        },
    }


async def _create_starter_spaces_and_tracks(
    user_id: str, selected: List[Any]
) -> Dict[str, Any]:
    """Concrete in-process App + Track provisioning (B4).

    Uses the extracted service-layer helpers — NO HTTP self-call, NO MCP
    recursion (per Pitfall 6). If ``selected`` is empty, defaults to the
    first library package with ``scope="app"``; if no library packages
    exist either, falls back to a generic "Starter App" with no library
    merge so the AC (>=1 App + >=1 Track) is still met.
    """
    user = await User.get(user_id)
    if not user:
        return {"app_ids": [], "track_ids": [], "workspace_id": None}

    workspace = await ensure_personal_workspace(user)

    # If the LLM passed no selections, default to first library
    # app-scope package — or, when no library packages exist, a single
    # "Starter App" so the AC is still met by construction.
    if not selected:
        packages = await list_library_packages()
        default = next((p for p in packages if p.scope == "app"), None)
        if default:
            selected = [{"id": default.id, "name": default.name}]
        else:
            selected = [{"id": None, "name": "Starter App"}]

    app_ids: List[str] = []
    track_ids: List[str] = []
    for sel in selected:
        package_id: Optional[str] = None
        pkg_name: Optional[str] = None
        if isinstance(sel, dict):
            package_id = sel.get("id") if sel.get("id") else None
            pkg_name = sel.get("name") if sel.get("name") else None
        app_node = await create_app_for_user(
            user_id=user_id,
            name=pkg_name or "Starter App",
            library_package_id=package_id,
            workspace_id=workspace.id,
        )
        app_ids.append(app_node.id)
        track = await create_track_in_space(
            user_id=user_id,
            title=f"{pkg_name or 'Starter'} — Inbox",
            app_id=app_node.id,
        )
        track_ids.append(track.id)

    return {
        "app_ids": app_ids,
        "track_ids": track_ids,
        "workspace_id": workspace.id,
    }
