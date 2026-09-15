"""`GET /workspaces/{id}/members` must not full-export User nodes.

Found while auditing the User-export surface. The collaborator lists in
``apps.py`` / ``tracks.py`` are the same leak on the same shape of endpoint and
are fixed separately (PR #71); the members list and the two watcher lists were
missed by the survey that preceded them — see the commit that added
``test_user_export_projection_responses.py`` for why (name-filtered grep) and
what replaced it (an AST pass over every ``export_node`` call site).

A raw ``export_node(User)`` carries ``preferences`` — which holds the
email-verification OTP slot (``{hash, expires_at, attempts}``) and,
historically, ``reset_token`` — plus ``notification_preferences``
(``phone_e164``), ``email_verified``, ``onboarded_at`` and
``active_workspace_id``. This endpoint is readable by anyone holding ``guest``
or above on the workspace, so that was every member's data to every member.

The second test is the other half, and the reason the fix is not a plain
one-line swap: the members page resolves "which of these rows is me" with
``isSamePrincipal(me, m.user_id || m.id)`` and keys rows the same way. The
public allowlist does not include ``user_id``, so swapping the call without
restoring it would break self-identification **silently** — no error, just a
page that no longer knows who you are. Both properties are asserted so a future
tidy-up cannot quietly drop either one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

_SOURCE = Path("app/api/workspaces.py")


def _members_handler_source() -> str:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "list_workspace_members"
        ):
            return ast.unparse(node)
    pytest.fail("list_workspace_members not found in app/api/workspaces.py")


def test_members_list_does_not_full_export_user():
    body = _members_handler_source()
    assert "public_user_view" in body, (
        "list_workspace_members must project members through public_user_view; "
        "a raw export leaks preferences (OTP slot), notification_preferences "
        "and email_verified to every member of the workspace"
    )
    assert (
        "export_node(u)" not in body
    ), "list_workspace_members still full-exports a User node"


def test_members_list_still_carries_user_id():
    """The allowlist omits user_id, and the members page needs it.

    `isSamePrincipal(me, m.user_id || m.id)` is how the page identifies the
    current user; without it the fix would degrade the UI rather than error.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    handler = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "list_workspace_members"
    )
    # Assert the ASSIGNMENT, not a mention. `"user_id" in source` passes on a
    # comment or a leftover reference, which would let the restore be deleted
    # while this test stayed green.
    assigns_user_id = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.slice, ast.Constant)
            and t.slice.value == "user_id"
            for t in node.targets
        )
        for node in ast.walk(handler)
    )
    assert assigns_user_id, (
        'no `data["user_id"] = …` assignment in list_workspace_members — the '
        "members page cannot identify the current user without it"
    )


def test_public_user_view_still_excludes_the_sensitive_fields():
    """Guards the allowlist itself, not just this call site.

    If `preferences` were ever added to PUBLIC_USER_FIELDS, every caller of the
    view would start leaking and the two tests above would still pass.
    """
    from app.api.utils import PUBLIC_USER_FIELDS

    for field in (
        "preferences",
        "notification_preferences",
        "password_hash",
        "email_verified",
    ):
        assert field not in PUBLIC_USER_FIELDS, (
            f"{field!r} is in PUBLIC_USER_FIELDS — the public projection is "
            "no longer safe to hand to another user"
        )
