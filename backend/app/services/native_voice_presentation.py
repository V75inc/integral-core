"""Bound ordinary presentation review to the exact current App configuration.

Complex widget/query/action changes require a visual review unless a current,
explicit standing App presentation grant authorizes the canonical writer.
"""

import asyncio
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from app.agentive.services.approval_policy import is_presentation_effect
from app.models.nodes import App
from app.services.native_voice_creation import is_spoken_note
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

_confirmation = ContextVar("voice_presentation_confirmation", default=None)


def _fingerprint(item):
    return hashlib.sha256(
        json.dumps(
            [
                item.token,
                item.user_id,
                item.workspace_id,
                item.session_id,
                item.kind,
                item.payload,
                item.origin_proof,
            ],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


async def presentation_review(item, work_id, run_id):
    """Construct a readable, revision-bound projection of an ordinary change."""
    from app.schemas.policy import Resource, Subject
    from app.services.app_home import home_revision_token
    from app.services.dashboard_service import get_dashboard
    from app.services.native_voice_review import (
        VoiceReviewReadout,
        spoken_review_action,
    )
    from app.services.policy_engine import evaluate

    if not is_presentation_effect(item.kind, item.payload):
        return None
    payload = item.payload
    app_id = payload["app_id"]
    location = await locate_resource(
        "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if not location or location.workspace_id != item.workspace_id:
        return None
    app = await App.get(app_id)
    if not app or not app.name or not is_spoken_note(app.name):
        return None
    decision = await evaluate(
        subject=Subject(kind="human", id=item.user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        return None
    target_id = app_id
    changes = []
    subject = ""
    if item.kind == "update_app":
        if home_revision_token(app) != payload["expected_home_revision"]:
            return None
        # A complete replacement of unknown widgets, actions or bindings is
        # not equivalent to a change of presentation labels.
        old = app.home_composition or {}
        new = payload["home"]
        if not isinstance(new, dict) or any(
            old.get(k) != new.get(k)
            for k in set(old) | set(new)
            if k not in {"title", "description"}
        ):
            return None
        for key in ("title", "description"):
            if old.get(key) != new.get(key):
                value = new.get(key)
                if not is_spoken_note(value):
                    return None
                changes.append(
                    f"set the Home {key} to “{value}”"
                    if value
                    else f"clear the Home {key}"
                )
        subject = f"in “{app.name}”"
    elif item.kind == "update_dashboard":
        target_id = payload["dashboard_id"]
        dashboard = await get_dashboard(
            user_id=item.user_id, app_id=app_id, dashboard_id=target_id
        )
        if (
            not dashboard
            or not dashboard.name
            or not is_spoken_note(dashboard.name)
            or dashboard.updated_at != payload["expected_dashboard_updated_at"]
        ):
            return None
        if "widgets" in payload and payload["widgets"] != dashboard.widgets:
            return None
        if "name" in payload and payload["name"] != dashboard.name:
            name = payload["name"]
            if not name or not is_spoken_note(name):
                return None
            changes.append(f"rename it to “{name}”")
        if "layout" in payload and payload["layout"] != dashboard.layout:
            layout = payload["layout"]
            if (
                not isinstance(layout, dict)
                or set(layout) != {"columns", "row_height"}
                or any(type(v) is not int for v in layout.values())
            ):
                return None
            changes.append(
                f'set {layout["columns"]} columns with a row height of {layout["row_height"]}'
            )
        if "is_default" in payload and payload["is_default"] != dashboard.is_default:
            if type(payload["is_default"]) is not bool:
                return None
            changes.append(
                "make it the default dashboard instead of the current default"
                if payload["is_default"]
                else "remove its default dashboard status"
            )
        subject = f"for the “{dashboard.name}” dashboard in “{app.name}”"
        if (
            len(changes) == 1
            and "name" in payload
            and payload["name"] != dashboard.name
        ):
            changes = [
                f'rename the “{dashboard.name}” dashboard in “{app.name}” to “{payload["name"]}”'
            ]
            subject = ""
    else:
        # Agent-created dashboards carry governed data queries. Do not hide
        # their composition behind a generic verbal approval.
        return None
    if not changes:
        return None
    text = (", and ".join(changes) + " " + subject).strip()
    if len(text) > 1000:
        return None
    return VoiceReviewReadout(
        text,
        work_id,
        run_id,
        item.token,
        target_id,
        _fingerprint(item),
        presentation={"kind": item.kind, "payload": payload},
        speech_action=spoken_review_action(
            text,
            payload,
            app.name,
            dashboard.name if item.kind == "update_dashboard" else "",
        ),
    )


@contextmanager
def confirmed_presentation(review):
    """Hold the independently captured review for only this decision task."""
    token = _confirmation.set(
        (asyncio.current_task(), review) if review.presentation else None
    )
    try:
        yield
    finally:
        _confirmation.reset(token)


def permits_presentation(item):
    """Check the exact pending proposal against its spoken confirmation."""
    binding = _confirmation.get()
    return bool(
        binding
        and binding[0] == asyncio.current_task()
        and binding[1].proposal_id == item.token
        and is_presentation_effect(item.kind, item.payload)
        and binding[1].fingerprint == _fingerprint(item)
    )


async def read_saved_presentation(review, principal, workspace):
    """Verify the requested presentation values, never infer success from speech."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.dashboard_service import get_dashboard
    from app.services.native_voice_review import VoiceAppliedReadout
    from app.services.permissions import can_view_app

    payload = review.presentation["payload"]
    app_id = payload["app_id"]
    with isolated_permissions_cache():
        location = await locate_resource(
            "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if (
            not location
            or location.workspace_id != workspace
            or not await can_view_app(principal, app_id)
        ):
            return None
        if review.presentation["kind"] == "update_app":
            app = await App.get(app_id)
            if not app or app.home_composition != payload["home"]:
                return None
            text = f"The Home configuration for “{app.name}” is updated."
        elif review.presentation["kind"] == "update_dashboard":
            board = await get_dashboard(
                user_id=principal, app_id=app_id, dashboard_id=payload["dashboard_id"]
            )
            if not board or any(
                getattr(board, key) != payload[key]
                for key in ("name", "layout", "widgets", "is_default")
                if key in payload
            ):
                return None
            text = f"The “{board.name}” dashboard is updated."
        else:
            return None
        return VoiceAppliedReadout(text, review, "", "")
