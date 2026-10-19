"""Two qualified runtime compositions, selected only by trusted startup.

This ceiling restricts operations; normal principal/workspace authorization still
applies. It is independent of products, account vendors, Apps and licensing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.runtime.operations import OPERATION_GROUPS


@dataclass(frozen=True)
class RuntimeComposition:
    preset: Literal["local", "hosted"]
    identity: Literal["installation_owner", "accounts"]
    workspace_kinds: frozenset[str]
    operation_groups: frozenset[str]

    @property
    def legacy_profile(self) -> str:
        return "desktop_single_user" if self.preset == "local" else "server"

    def supports(self, capability: str) -> bool:
        return capability in self.operation_groups

    def supports_action(self, action: str) -> bool:
        if action.startswith("app:"):
            # The policy engine validates the exact installed App/resource and
            # operation. This classification supplies no permission grant.
            app_id, separator, operation = action[4:].rpartition(":")
            return bool(app_id and separator and operation)
        group = OPERATION_GROUPS.get(action)
        return group is not None and self.supports(group)


LOCAL = RuntimeComposition(
    "local", "installation_owner", frozenset({"personal"}), frozenset({"records"})
)
HOSTED = RuntimeComposition(
    "hosted",
    "accounts",
    frozenset({"personal", "organization"}),
    frozenset({"records", "identity_admission", "collaboration", "public_sharing"}),
)


def runtime() -> RuntimeComposition:
    """Resolve the immutable preset; retain existing installation/profile names."""
    from app.config import settings

    preset = settings.INTEGRAL_RUNTIME_PRESET
    legacy = settings.INTEGRAL_DEPLOYMENT_PROFILE
    if preset is None:
        preset = {"server": "hosted", "desktop_single_user": "local"}.get(legacy)
    if preset not in {"local", "hosted"} or (
        legacy == "desktop_single_user" and preset != "local"
    ):
        raise RuntimeError("Conflicting or unsupported runtime composition")
    return LOCAL if preset == "local" else HOSTED


def require_capability(capability: str) -> None:
    """Deny before reads or mutations, including direct service/job/tool calls."""
    if not runtime().supports(capability):
        from app.api.errors import InsufficientPermissionsError

        raise InsufficientPermissionsError(
            message=f"This runtime does not support {capability}.",
            details={"deployment_profile": runtime().legacy_profile},
        )


def owner_ids() -> tuple[str, str]:
    """Keep installation-bound identifiers stable across runtime refactors."""
    from app.config import settings

    identity = settings.INTEGRAL_DESKTOP_INSTALLATION_ID
    if (
        not identity
        or len(identity) != 32
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise RuntimeError("Installation identity is missing or invalid")
    return f"o.User.desktop-{identity}", f"n.User.desktop-{identity}"


def admits_human(principal: str) -> bool:
    """Identity admission is additional to token and workspace validation."""
    return runtime().identity == "accounts" or principal in owner_ids()


def capabilities() -> dict:
    """Public presentation contract; legacy fields remain compatible."""
    selected = runtime()
    return {
        "profile": selected.legacy_profile,
        "identity": (
            "local_owner" if selected.identity == "installation_owner" else "account"
        ),
        "runtime": {
            "contract_version": 1,
            "preset": selected.preset,
            "identity": selected.identity,
            "workspace_kinds": sorted(selected.workspace_kinds),
        },
        "features": {
            "collaboration": selected.supports("collaboration"),
            "public_sharing": selected.supports("public_sharing"),
            "organization_workspaces": "organization" in selected.workspace_kinds,
            "app_extensions": True,
            "app_authoring": True,
            "manual_records": True,
        },
    }
