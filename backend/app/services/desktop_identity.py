"""Compatibility aliases; persisted identity names remain unchanged."""

from app.runtime.local_identity import (
    DesktopIdentity,
)
from app.runtime.local_identity import (  # noqa: F401
    bootstrap_owner as bootstrap_desktop_owner,
)

__all__ = [
    "DesktopIdentity",
    "bootstrap_desktop_owner",
    "native_session",
    "preserve_owner_workspace",
]
from app.runtime.local_identity import issue_session as native_session
from app.runtime.local_identity import (
    preserve_owner_workspace,
)
