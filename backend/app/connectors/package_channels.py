"""Compose the fixed Core-owned first-party adapters at startup."""

from __future__ import annotations

from types import ModuleType

from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.package_discovery import current_admission, rescan

_grant_modules: dict[str, ModuleType] = {}


_GRANT_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_key_ciphertext",
        "webhook_secret",
        "webhook_secret_ciphertext",
    }
)


def _grant_view(module, binding) -> dict | None:
    status = getattr(module, "grant_status", None)
    if not callable(status):
        return None
    view = status(binding)
    if not isinstance(view, dict) or _GRANT_SECRET_KEYS & view.keys():
        return None
    return view


def read_host_grant(package_id: str, binding) -> dict | None:
    """Read a host-managed package grant. Secrets are dropped. No installation is created."""
    module = _grant_modules.get(package_id)
    if module is None:
        return None
    return _grant_view(module, binding)


async def read_host_grant_for_workspace(
    package_id: str, workspace_id: str
) -> dict | None:
    """Read a grant using the package's own binding loader when it declares one."""
    module = _grant_modules.get(package_id)
    if module is None:
        return None
    binding = None
    loader = getattr(module, "workspace_binding", None)
    if callable(loader):
        found = loader(workspace_id)
        binding = await found if hasattr(found, "__await__") else found
    return _grant_view(module, binding)


def register_packaged_managed_text(registry: ChannelPackageRegistry) -> int:
    """Wire Core-owned adapters using fixed imports, never manifest references."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.agentive.connectors.gmail import GmailConnector
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.bundled_packages.whatsapp.adapter import grants
    from app.services.connectors.registry import (
        get_sync_connector,
        register_sync_connector,
    )
    from app.web.web_chat_management_assembly import build_web_chat_management_package

    admission = current_admission()
    if not admission.packages and not admission.rejected:
        admission = rescan()
    sync_adapters = {
        "github_issues": GitHubIssuesConnector,
        "gmail": GmailConnector,
        "quickbooks": QuickBooksConnector,
    }
    channels = {
        "public_web_chat": build_web_chat_management_package,
        "whatsapp": grants.package,
    }
    for package_id in {*sync_adapters, *channels}:
        manifest = admission.packages.get(package_id)
        if not manifest or manifest.get("trust", {}).get("tier") != "trusted":
            raise RuntimeError(f"Core connector package {package_id} was not admitted")
    for package_id, adapter in sync_adapters.items():
        try:
            registered = get_sync_connector(package_id)
        except ValueError:
            register_sync_connector(package_id)(adapter)
        else:
            if type(registered) is not adapter:
                raise ValueError(f"{package_id} registered a different Core adapter")
    for factory in channels.values():
        registry.register(factory())
    _grant_modules.clear()
    _grant_modules["whatsapp"] = grants
    return len(channels)
