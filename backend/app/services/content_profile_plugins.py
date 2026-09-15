"""Discovery and verification for ContentProfile code plugins.

Pillar 1 escape-hatch (Pillar 4 in some readings) of the agent-authorable
substrate. Plugins ship genuinely-new primitive renderers (Mermaid widget,
3D molecule view, regex-validated email field, etc.) that cannot be
expressed as declarative composites of existing primitives. They are
**rare** by design — the agent's primary surface is composites declared
inline in a manifest.

Discovery sources:

  1. Python entry points in group ``integral.content_profile.plugins``.
     A plugin's ``module:register`` callable receives the registries and
     calls ``register_field_type()`` / ``register_view_type()``.
  2. Directory scan of ``backend/app/plugins/``. Each subdirectory is
     treated as a package; if it exposes a top-level ``register`` function,
     it is invoked.

Signature policy:

  - In dev (``INTEGRAL_PLUGIN_PUBKEY`` unset), all discovered plugins load
    unconditionally. A warning is logged listing what was registered.
  - In prod (``INTEGRAL_PLUGIN_PUBKEY`` set), each plugin must ship a
    ``signature`` file alongside its package metadata; signatures are
    verified via Ed25519 against the configured pubkey. Unsigned plugins
    are skipped with an error.

Entry-point and signature integration are intentionally narrow in v1; the
discovery surface exists so the door is open without requiring the full
signing pipeline up front.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services import content_profile_field_types as field_type_registry
from app.services import content_profile_wizard_steps as wizard_step_registry
from app.views import content_profile_view_types as view_type_registry

logger = logging.getLogger(__name__)

PLUGIN_ENTRY_POINT_GROUP = "integral.content_profile.plugins"
PLUGIN_PUBKEY_ENV = "INTEGRAL_PLUGIN_PUBKEY"
DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"


_DISCOVERED: List[Dict[str, Any]] = []


def discovered_plugins() -> List[Dict[str, Any]]:
    """Return descriptors for every plugin loaded this process.

    Each descriptor: ``{ id, source, signed, registered_field_types,
    registered_view_types }``.
    """
    return list(_DISCOVERED)


def _verify_signature(*, plugin_id: str, signature_path: Optional[Path]) -> bool:
    """Verify a plugin's Ed25519 signature against ``INTEGRAL_PLUGIN_PUBKEY``.

    Returns True when the policy is satisfied. Dev mode (no pubkey set) is
    permissive — returns True with a logged warning. Prod mode without a
    valid signature returns False.
    """
    pubkey_b64 = os.environ.get(PLUGIN_PUBKEY_ENV)
    if not pubkey_b64:
        logger.warning(
            "content-profile plugin '%s' loaded WITHOUT signature verification "
            "(INTEGRAL_PLUGIN_PUBKEY unset; dev mode)",
            plugin_id,
        )
        return True
    if signature_path is None or not signature_path.exists():
        logger.error(
            "content-profile plugin '%s' missing signature file; refusing to load",
            plugin_id,
        )
        return False
    try:
        from nacl.encoding import Base64Encoder  # type: ignore[import-not-found]
        from nacl.signing import VerifyKey  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "content-profile plugin '%s' requires PyNaCl for signature verification",
            plugin_id,
        )
        return False
    try:
        verify_key = VerifyKey(pubkey_b64.encode(), encoder=Base64Encoder)
        signature_bytes = signature_path.read_bytes()
        # The signature file format is: <signature_bytes><payload_bytes> —
        # the payload is the SHA-256 of the plugin's __init__.py content.
        # Real implementation would package a manifest; this is the v1 stub.
        verify_key.verify(signature_bytes)
        return True
    except Exception as exc:  # pragma: no cover - exercised in prod
        logger.error(
            "content-profile plugin '%s' signature verification failed: %s",
            plugin_id,
            exc,
        )
        return False


def _register_plugin_module(
    *,
    plugin_id: str,
    module: Any,
    source: str,
    signed: bool,
) -> None:
    register_fn = getattr(module, "register", None)
    if not callable(register_fn):
        logger.warning(
            "content-profile plugin '%s' has no callable 'register' function; skipping",
            plugin_id,
        )
        return
    fields_before = set(field_type_registry.allowed_keys())
    views_before = set(view_type_registry.allowed_keys())
    # Every registry is offered, but only passed to a `register()` that
    # actually declares the kwarg — introspected, not hardcoded, so adding
    # a new registry here (like `wizard_step_registry`) never requires
    # touching a plugin that doesn't care about it. Matches this module's
    # own "no existing registry file is edited to wire in a plugin" stance,
    # extended to "adding a registry doesn't require editing every plugin."
    available_kwargs = {
        "field_type_registry": field_type_registry,
        "view_type_registry": view_type_registry,
        "wizard_step_registry": wizard_step_registry,
    }
    try:
        params = inspect.signature(register_fn).parameters
        has_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        call_kwargs = (
            available_kwargs
            if has_var_kwargs
            else {k: v for k, v in available_kwargs.items() if k in params}
        )
    except (TypeError, ValueError):  # pragma: no cover — builtins/C callables
        call_kwargs = available_kwargs
    try:
        register_fn(**call_kwargs)
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "content-profile plugin '%s' raised during registration: %s",
            plugin_id,
            exc,
        )
        return
    fields_added = sorted(set(field_type_registry.allowed_keys()) - fields_before)
    views_added = sorted(set(view_type_registry.allowed_keys()) - views_before)
    _DISCOVERED.append(
        {
            "id": plugin_id,
            "source": source,
            "signed": signed,
            "registered_field_types": fields_added,
            "registered_view_types": views_added,
        }
    )
    logger.info(
        "content-profile plugin '%s' loaded (source=%s, signed=%s, "
        "field_types+%d, view_types+%d)",
        plugin_id,
        source,
        signed,
        len(fields_added),
        len(views_added),
    )


def _discover_via_entry_points() -> None:
    try:
        from importlib.metadata import entry_points  # py3.10+
    except ImportError:  # pragma: no cover
        return
    eps: Any
    try:
        eps = entry_points(group=PLUGIN_ENTRY_POINT_GROUP)  # type: ignore[call-arg]
    except TypeError:  # py<3.10 fallback shape
        eps = entry_points().get(PLUGIN_ENTRY_POINT_GROUP, [])
    for ep in eps:
        try:
            module = ep.load()
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "content-profile plugin entry point '%s' failed to load: %s",
                ep.name,
                exc,
            )
            continue
        signed = _verify_signature(plugin_id=ep.name, signature_path=None)
        if not signed and os.environ.get(PLUGIN_PUBKEY_ENV):
            continue
        _register_plugin_module(
            plugin_id=ep.name,
            module=module,
            source="entry_point",
            signed=signed,
        )


def _discover_via_directory(directory: Optional[Path] = None) -> None:
    plugin_dir = directory or DEFAULT_PLUGIN_DIR
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        return
    for child in sorted(plugin_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        init_path = child / "__init__.py"
        if not init_path.exists():
            continue
        plugin_id = child.name
        signature_path = child / "signature.bin"
        signed = _verify_signature(
            plugin_id=plugin_id,
            signature_path=signature_path if signature_path.exists() else None,
        )
        if not signed and os.environ.get(PLUGIN_PUBKEY_ENV):
            continue
        # Add the parent dir to sys.path temporarily so a relative import works.
        if str(plugin_dir) not in sys.path:
            sys.path.insert(0, str(plugin_dir))
        try:
            module = importlib.import_module(plugin_id)
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "content-profile plugin directory '%s' failed to import: %s",
                plugin_id,
                exc,
            )
            continue
        _register_plugin_module(
            plugin_id=plugin_id,
            module=module,
            source="directory",
            signed=signed,
        )


def discover_and_register_plugins(
    *, directory: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Discover plugins from entry points + directory scan.

    Idempotent: subsequent calls return the already-registered descriptors
    rather than re-loading modules.

    F0: also walks ``<package_root>/*/plugins/`` so App-owned plugins
    load without living under Core's ``backend/app/plugins/``.
    """
    if _DISCOVERED:
        return discovered_plugins()
    _discover_via_entry_points()
    _discover_via_directory(directory=directory)
    if directory is None:
        try:
            from app.services.package_paths import resolve_package_paths

            for root in resolve_package_paths():
                for bundle_plugins in sorted(root.glob("*/plugins")):
                    if bundle_plugins.is_dir():
                        _discover_via_directory(directory=bundle_plugins)
        except Exception as exc:  # noqa: BLE001
            logger.warning("package-root plugin discovery failed: %s", exc)
    return discovered_plugins()


def reset_discovered_for_tests() -> None:
    """Test hook: clear the discovered list so tests can re-run discovery."""
    _DISCOVERED.clear()
