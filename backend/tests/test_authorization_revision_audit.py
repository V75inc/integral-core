"""Audit: every access-affecting write path is ordered by a revision-bumping scope.

A future access-inventory cache can only trust WorkspaceAuthorizationState.revision
if every write that changes who may do what bumps it. These tests read the source
(no database) and fail when a new writer or a new non-bumping scope appears without
a deliberate entry here. Nothing in this file adds a cache.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Models whose rows decide access. Writing one changes what some principal may do.
ACCESS_MODELS = {
    "IS_MEMBER_OF",
    "COLLABORATES_ON",
    "ShareGrant",
    "ResourceAccessBlock",
    "EXCLUDED_FROM",
    "WorkspaceSuspension",
    "OWNS",
    "AccessGroupMember",
    "WorkspaceAccessGroup",
    "HasPolicy",
    "HasGovernancePolicy",
    "HasShareLink",
    "INVITED_TO",
    "HasActionBinding",
    "ActionBindingSubject",
    "OwnershipProposal",
    "OwnershipGrantHistory",
    "MembershipGrantHistory",
    "CollaborationGrantHistory",
}
WRITES = {"create", "connect", "disconnect", "delete", "save", "delete_edge", "update"}
# A module that mentions one of these orders its writes with the bumping scope.
BUMPING = (
    "membership_write_scope",
    "sharing_write_scope",
    "invitation_storage_scope",  # wraps authorization_scope(authority_mutation=True)
    "authority_mutation=True",
)

# Writers with no bumping scope of their own, with the reason it is safe today.
# A new entry here is a review decision: say why no revision bump is needed.
UNSCOPED_WRITERS = {
    "api/auth.py": "first sign-in creates a brand new workspace and its owner membership",
    "api/workspaces.py": "creates a brand new workspace and its owner membership",
    "runtime/local_identity.py": "local identity bootstrap of a new workspace",
    "services/personal_workspace.py": "creates a brand new personal workspace",
    "services/agent_scratch.py": "OWNS edge for a new scratch resource, no grant change",
    "services/app_graph.py": "OWNS edge for a new app node, no grant change",
    "services/operational_model_merge.py": "OWNS edge for merged models, no grant change",
    "services/track_restructuring.py": "OWNS edge moved inside a caller membership scope",
    "services/track_service.py": "OWNS edge for a new track inside track_writer scope",
    "services/ownership_transfer.py": "called by workspace_departure inside its member scope",
    "services/library_model_creation.py": (
        "creator admin grant on a model created in the same call; "
        "caller checks live membership. Not covered by a revision bump."
    ),
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text())


def _writes_access_models(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in WRITES
            and isinstance(func.value, ast.Name)
            and func.value.id in ACCESS_MODELS
        ):
            found.add(f"{func.value.id}.{func.attr}")
        if isinstance(func, ast.Attribute) and func.attr in {"connect", "disconnect"}:
            for kw in node.keywords:
                if (
                    kw.arg == "edge"
                    and isinstance(kw.value, ast.Name)
                    and kw.value.id in ACCESS_MODELS
                ):
                    found.add(f"{kw.value.id}.{func.attr}")
        if isinstance(func, ast.Name) and func.id in ACCESS_MODELS:
            found.add(f"{func.id}()")
    return found


def _app_files():
    for path in sorted(APP.rglob("*.py")):
        if path.name in {"nodes.py", "edges.py"} and path.parent.name == "models":
            continue
        yield path


def test_membership_write_scope_bumps_the_revision():
    source = (APP / "services/membership_write_scope.py").read_text()
    assert re.search(
        r"authorization_scope\(\s*workspace_id,\s*authority_mutation=True\s*\)", source
    )


def test_authorization_scope_only_increments_for_authority_mutations():
    source = (APP / "services/authorization_scope.py").read_text()
    assert '"$inc": {"context.revision": 1 if authority_mutation else 0}' in source
    assert "authority_mutation: bool = False" in source


def test_non_bumping_scope_callers_are_only_fences_and_content_writes():
    """These take the scope to read a revision or order content, never to change access."""
    expected = {
        "services/sharing.py",  # list access: read fence
        "services/release_admission.py",  # releases a snapshot under a read fence
        "services/event_access.py",  # notification reference check
        "services/entry_effect_scope.py",  # entry content effects
        "services/kanban_columns.py",  # board column content
        "services/bundle_cleanup_outbox.py",  # cleanup outbox
        "services/app_operations/history.py",  # operation history
        "services/access_delegation.py",  # owner-checked app function fence
    }
    found = set()
    for path in _app_files():
        tree = _tree(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "authorization_scope"
            ):
                bumps = any(
                    kw.arg == "authority_mutation"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in node.keywords
                )
                if not bumps:
                    found.add(path.relative_to(APP).as_posix())
    assert found == expected, (
        "A new authorization_scope caller does not bump the revision. If it "
        "changes access, pass authority_mutation=True; otherwise list it here."
    )


def test_every_access_model_writer_is_scoped_or_listed_with_a_reason():
    unscoped = {}
    for path in _app_files():
        writes = _writes_access_models(_tree(path))
        if writes and not any(token in path.read_text() for token in BUMPING):
            unscoped[path.relative_to(APP).as_posix()] = sorted(writes)
    assert set(unscoped) == set(UNSCOPED_WRITERS), (
        f"Unscoped access writers changed: {sorted(set(unscoped) ^ set(UNSCOPED_WRITERS))}. "
        "Scope the new writer with membership_write_scope, or list it with a reason."
    )
