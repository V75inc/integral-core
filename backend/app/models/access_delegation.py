"""Workspace-rooted groups and definition-bound App action grants."""

from jvspatial.core import Edge, Node
from pydantic import Field


class WorkspaceAccessGroup(Node):
    workspace_id: str
    name: str
    created_by: str
    created_at: str
    revision: int = 1


class HasAccessGroup(Edge):
    pass


class AccessGroupMember(Edge):
    workspace_id: str
    membership_fingerprint: str
    granted_by: str
    granted_at: str


class AppActionBinding(Node):
    app_id: str
    workspace_id: str
    definition_id: str
    definition_fingerprint: str
    subject_kind: str
    subject_id: str
    role: str
    membership_fingerprint: str = ""
    conditions: list[dict] = Field(default_factory=list)
    expires_at: str | None = None
    created_by: str
    created_at: str
    revision: int = 1
    active: bool = True


class HasActionBinding(Edge):
    pass


class ActionBindingSubject(Edge):
    subject_kind: str


ACTION_BINDING_SUBJECT = ActionBindingSubject
HAS_ACCESS_GROUP = HasAccessGroup
ACCESS_GROUP_MEMBER = AccessGroupMember
HAS_ACTION_BINDING = HasActionBinding
