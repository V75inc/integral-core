"""POL-01 invariant: no direct can_view_*/can_edit_*/can_delete_* helper calls
remain in backend/app/api/ or backend/app/agentive/api/.

The single authorization entry point is ``policy_engine.evaluate(...)``. Any
direct call to the legacy helpers from a controller layer fails CI.

The helpers themselves remain in ``app/services/permissions.py`` as DEPRECATED
wrappers; the engine's default-human path delegates to them. This gate
enforces that the deprecation is observed at the controller layer.

Mirrors the Phase 2 ``test_change_event_no_bypass.py`` AST-walk pattern (commit
``10769ea``).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Helper names that MUST NOT appear as direct call sites under app/api/ or
# app/agentive/api/. The engine delegates to them internally — that is fine.
LEGACY_HELPER_NAMES: set[str] = {
    "can_view_track",
    "can_edit_track",
    "can_delete_track",
    "can_view_app",
    "can_edit_app",
    "can_delete_app",
    "can_view_entry",
    "can_edit_entry",
    "can_view_view",
    "can_edit_view",
}

# (file_relative_path, function_name) tuples that legitimately bypass the rule.
# Every entry MUST have a justifying inline comment. Post-Plan 03-02 sweep:
# this is intentionally empty — there are no legitimate direct callers.
ALLOW_LIST: set[tuple[str, str]] = set()


def _legacy_aliases_from_tree(tree: ast.AST) -> dict[str, str]:
    """Map local names → canonical helper for ``from … import can_x as alias``."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in LEGACY_HELPER_NAMES:
                local = alias.asname or alias.name
                aliases[local] = alias.name
    return aliases


def _is_helper_call(
    node: ast.expr, aliases: dict[str, str] | None = None
) -> str | None:
    """Return the helper name if ``node`` is a Call to one of LEGACY_HELPER_NAMES.

    Detects both direct-name calls (``can_view_track(...)``) and attribute
    calls (``permissions.can_view_track(...)``). Also resolves
    ``from … import can_delete_app as app_delete_allowed`` aliases.
    """
    if not isinstance(node, ast.Call):
        return None
    aliases = aliases or {}
    func = node.func
    # Direct name call: can_view_track(...) or aliased local name
    if isinstance(func, ast.Name):
        if func.id in LEGACY_HELPER_NAMES:
            return func.id
        if func.id in aliases:
            return aliases[func.id]
        return None
    # Attribute call: permissions.can_view_track(...) — also caught
    if isinstance(func, ast.Attribute) and func.attr in LEGACY_HELPER_NAMES:
        return func.attr
    return None


def _scan_function_for_direct_calls(
    func: ast.AST, aliases: dict[str, str] | None = None
) -> list[str]:
    """Walk every Call node in ``func``'s body and return offending helper names."""
    offenders: list[str] = []
    for node in ast.walk(func):
        helper = _is_helper_call(node, aliases)
        if helper:
            offenders.append(helper)
    return offenders


def _repo_root() -> Path:
    """Path to the repo root (parent of ``backend/``)."""
    # tests/test_no_legacy_helper_calls.py → backend → repo
    return Path(__file__).resolve().parents[2]


def test_no_direct_legacy_helper_calls_in_app_api():
    """No async function under backend/app/api/ may call can_view_*/can_edit_*/can_delete_* directly."""
    repo_root = _repo_root()
    api_dir = repo_root / "backend" / "app" / "api"
    offenders: list[str] = []

    for py_path in api_dir.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
        if "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        aliases = _legacy_aliases_from_tree(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if (rel, node.name) in ALLOW_LIST:
                continue
            for helper in _scan_function_for_direct_calls(node, aliases):
                offenders.append(f"{rel}::{node.name} -> {helper}()")

    assert not offenders, (
        "POL-01 regression — direct legacy helper call in app/api/:\n"
        + "\n".join(offenders)
        + "\n\nUse `await policy_engine.evaluate(...)` instead. See "
        "Plan 03-02 mapping table for the action / resource shape per helper."
    )


def test_no_direct_legacy_helper_calls_in_app_agentive_api():
    """No async function under backend/app/agentive/api/ may call can_view_*/can_edit_*/can_delete_* directly."""
    repo_root = _repo_root()
    agentive_api = repo_root / "backend" / "app" / "agentive" / "api"
    offenders: list[str] = []

    if not agentive_api.exists():
        return  # No agentive surface — gate is no-op

    for py_path in agentive_api.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
        if "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        aliases = _legacy_aliases_from_tree(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if (rel, node.name) in ALLOW_LIST:
                continue
            for helper in _scan_function_for_direct_calls(node, aliases):
                offenders.append(f"{rel}::{node.name} -> {helper}()")

    assert not offenders, (
        "POL-01 regression — direct legacy helper call in app/agentive/api/:\n"
        + "\n".join(offenders)
    )


def test_no_direct_legacy_helper_calls_in_event_subscription_registry():
    """Phase 2 _user_permitted_for_scope drop-in site is migrated; no direct can_* calls remain."""
    repo_root = _repo_root()
    target = (
        repo_root / "backend" / "app" / "services" / "event_subscription_registry.py"
    )
    offenders: list[str] = []

    try:
        tree = ast.parse(target.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError):
        return  # Module not present — gate is no-op

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for helper in _scan_function_for_direct_calls(node):
            offenders.append(f"{node.name} -> {helper}()")

    assert not offenders, (
        "POL-01 regression — direct legacy helper call in event_subscription_registry.py:\n"
        + "\n".join(offenders)
    )


def test_allow_list_entries_resolve():
    """Every (file, function) in ALLOW_LIST must resolve to a real file/function.

    Prevents stale allow-list entries from masking real regressions.
    """
    repo_root = _repo_root()
    unresolved: list[str] = []

    for rel, fn_name in ALLOW_LIST:
        path = repo_root / rel
        if not path.exists():
            unresolved.append(f"{rel} (file missing)")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            unresolved.append(f"{rel} (parse error)")
            continue
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if fn_name not in names:
            unresolved.append(f"{rel}::{fn_name} (function missing)")

    assert not unresolved, "ALLOW_LIST staleness:\n" + "\n".join(unresolved)


def test_legacy_helpers_still_defined_in_permissions_module():
    """services/permissions.py is the canonical helper definition site.

    The gate forbids direct CALLS in app/api/, not the DEFINITIONS in services/.
    This test asserts the helpers still exist (Plan 7 cleanup deletes them).
    """
    repo_root = _repo_root()
    perms = repo_root / "backend" / "app" / "services" / "permissions.py"
    src = perms.read_text(encoding="utf-8")
    for helper in LEGACY_HELPER_NAMES:
        assert f"async def {helper}(" in src, (
            f"{helper} missing from services/permissions.py — "
            "the engine's default-human path delegates to these helpers; "
            "removing them breaks POL-03 parity."
        )
