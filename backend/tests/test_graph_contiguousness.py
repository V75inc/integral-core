"""Phase 10.5 Plan 10.5-00 — Graph Contiguousness substrate gate (I-GRAPH-01).

Five regression + gate tests covering the ``app.services.graph_reachability``
public surface:

1. ``test_walker_reaches_canonical_chain`` — boot canonical fixture, run
   the walker, assert ``reachable_counts`` includes every rooted class.
2. ``test_orphan_detection_finds_known_orphan`` — seed a bare orphan
   (Notification without ``HAS_NOTIFICATION`` edge), run ``audit_orphans()``
   against an EMPTY allowlist, assert the class appears.
3. ``test_allowlist_suppresses_known_orphans`` — same seed, run against
   the real allowlist, assert result is empty.
4. ``test_ast_gate_every_create_site_wires_edge`` — AST-style regex
   sweep over ``backend/app/`` asserting every ``<Class>.create(`` and
   every ``<Class>(...)`` + ``await <var>.save()`` constructor persist
   is followed by a same-function ``.connect(`` OR a known wiring-helper
   call OR the class is on the allowlist.
5. ``test_no_unknown_node_classes_are_orphan`` — runs ``run_audit`` on
   the canonical fixture; asserts ``report.orphans`` is empty (every
   detached class must be on the allowlist).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from app.services.graph_reachability import (
    ALLOWLIST_PATH,
    AuditReport,
    GraphContiguousnessViolation,
    GraphReachabilityWalker,
    _all_node_classes,
    _parse_allowlist,
    audit_orphans,
    run_audit,
)

# ---------------------------------------------------------------------------
# Test 1 — walker reaches the canonical chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walker_reaches_canonical_chain():
    """``ensure_integral_app_graph`` (run by setup_test_db autouse) wires
    Root → IntegralApp → {Users, ContentProfiles, Invitations, Workspaces}.
    The walker spawned from Root must reach every one of those.
    """
    report = await run_audit(raise_on_violation=False)

    assert report.walker_runtime_seconds >= 0.0
    assert "IntegralApp" in report.reachable_counts
    assert report.reachable_counts["IntegralApp"] == 1
    # Each top-level registry is a singleton wired structurally to IntegralApp.
    for registry_class in ("Users", "ContentProfiles", "Invitations", "Workspaces"):
        assert registry_class in report.reachable_counts, (
            f"walker did not reach {registry_class} registry from Root — "
            f"reachable_counts={report.reachable_counts}"
        )
        assert report.reachable_counts[registry_class] >= 1


# ---------------------------------------------------------------------------
# Test 2 — orphan detection finds a bare unconnected node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_detection_finds_known_orphan():
    """Seed a Notification WITHOUT the canonical ``HAS_NOTIFICATION`` edge.

    Against an EMPTY allowlist, the audit must surface the orphan.
    """
    from app.models.nodes import Notification

    notif = await Notification.create(
        user_id="bare-user-id-for-orphan-test",
        type="system",
        content="orphan probe",
        read=False,
    )
    assert notif.id, "Notification.create must return a persisted node with id"

    # Empty allowlist file in a temp path so the real allowlist is ignored.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("# empty allowlist for this test\n")
        empty_allowlist = Path(tmp.name)
    try:
        orphans = await audit_orphans(allowlist_path=empty_allowlist)
    finally:
        empty_allowlist.unlink(missing_ok=True)

    assert (
        "Notification" in orphans
    ), f"audit_orphans did not surface bare Notification — got {orphans}"
    assert notif.id in orphans["Notification"]


# ---------------------------------------------------------------------------
# Test 3 — allowlist suppresses known in-flight orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_suppresses_known_orphans():
    """Seed a bare orphan against a TEMP allowlist that lists its class —
    audit_orphans must return empty.

    Uses a temp allowlist (not the shipped one) so the test stays stable
    as classes drain from the real allowlist across Plans 10.5-01..08.
    """
    from app.models.nodes import Notification

    notif = await Notification.create(
        user_id="bare-user-id-for-allowlist-suppression-test",
        type="system",
        content="orphan probe — temp allowlist suppresses",
        read=False,
    )
    assert notif.id

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("# temp allowlist for suppression regression\n")
        tmp.write("Notification  # reason: temp-suppression-test\n")
        temp_allowlist = Path(tmp.name)
    try:
        orphans = await audit_orphans(allowlist_path=temp_allowlist)
    finally:
        temp_allowlist.unlink(missing_ok=True)

    assert (
        "Notification" not in orphans
    ), f"temp allowlist failed to suppress Notification — got {orphans}"


# ---------------------------------------------------------------------------
# Test 4 — AST gate: every <Class>.create( has a wire OR is allowlisted
# ---------------------------------------------------------------------------


# Known wiring-helper call names that materialize a structural edge as a
# side-effect (so a ``<Class>.create(...)`` followed by one of these is
# considered contiguousness-wired even without a direct ``.connect(``).
_KNOWN_WIRING_HELPERS = {
    "link_notification",
    "ensure_catalog_edge",
    "catalog_user",
    "catalog_app",
    # Wires User -OWNS-> App (and repairs workspace_id / owner_user_id).
    "wire_app_owner",
    "catalog_track",
    "catalog_workspace",
    "catalog_invitation",
    "catalog_chat_thread",
    "catalog_view_under_track",
    "catalog_template_node_under_content_profile",
    "ensure_workspace_branches",
    "ensure_app_attached_content_profile",
    "ensure_track_attached_content_profile",
    "get_or_create_views_registry_for_content_profile",
    # Plan 10.5-01: centralizes Notification creation + HAS_NOTIFICATION
    "create_notification",
    # Plan 10.5-06: scope-discriminated AgentConfig wire dispatcher
    "wire_agent_config_attachment_edge",
    "register_agent_config",
    # Plan 10.5-02: generic edge-upsert helper (HAS_SHARE_LINK wires,
    # invitation/access wires, etc). Used wherever a structural edge
    # is wired via the canonical edge_upsert.ensure_edge entry point
    # instead of node.connect(...) directly.
    "ensure_edge",
    "_wire_share_link_to_resource",
}


# Node classes that are NOT subject to the contiguousness gate today
# (allowlisted in ``backend/.ci/graph_contiguousness_allowlist.txt`` or
# infrastructure singletons that the test doesn't need to gate).
def _gate_exempt_classes() -> set[str]:
    exempt = _parse_allowlist()
    # Infrastructure / registry singletons created only by
    # ``ensure_integral_app_graph`` / ``ensure_workspace_branches``;
    # their wire IS the helper function, so the regex below catches them
    # via _KNOWN_WIRING_HELPERS. Listing them here is defense-in-depth
    # in case the regex misses the helper-call paths.
    exempt.update(
        {
            "IntegralApp",
            "Users",
            "ContentProfiles",
            "Invitations",
            "Workspaces",
            "Apps",
            "Tracks",
            "ChatThreads",
            "Views",
        }
    )
    return exempt


def _line_is_inside_docstring(lines: list[str], target_idx: int) -> bool:
    """Heuristic: count ``\"\"\"`` triple-quote delimiters before ``target_idx``.

    Odd count = currently inside a docstring / triple-quoted string. Not a
    full Python lexer (won't catch single-quoted triples or escaped
    sequences) but sufficient to filter the docstring-example false
    positives surfaced by the gate (e.g. ``app = await App.create(...)``
    inside ``InstallTransaction.__doc__``).
    """
    count = 0
    for i in range(target_idx):
        count += lines[i].count('"""')
    return count % 2 == 1


func_def_re = re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z_][\w]*)\s*\(")


def _enclosing_function_body(lines: list[str], idx: int) -> str:
    """Return the source slice for the function containing ``lines[idx]``."""
    window_start = max(0, idx - 5)
    enclosing_indent: Optional[int] = None
    for back in range(idx, -1, -1):
        m_def = func_def_re.match(lines[back])
        if m_def:
            window_start = back
            enclosing_indent = len(lines[back]) - len(lines[back].lstrip())
            break
    window_end = len(lines)
    if enclosing_indent is not None:
        for fwd in range(idx + 1, len(lines)):
            m_sib = func_def_re.match(lines[fwd])
            if m_sib:
                sib_indent = len(lines[fwd]) - len(lines[fwd].lstrip())
                if sib_indent == enclosing_indent:
                    window_end = fwd
                    break
    return "\n".join(lines[window_start:window_end])


def _find_create_sites(path: Path) -> list[tuple[Path, int, str, str]]:
    """Find ``<ClassName>.create(`` matches under ``path``.

    Returns list of ``(file_path, line_no, class_name, enclosing_function_body)``.

    Skips matches that fall inside a ``\"\"\"...\"\"\"`` docstring block — those
    are usage examples, not real call sites.
    """
    create_re = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.create\(")

    out: list[tuple[Path, int, str, str]] = []
    for py_file in path.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text()
        except Exception:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for m in create_re.finditer(line):
                cls = m.group(1)
                if cls in {"Test", "AsyncClient", "BaseModel", "Path", "Mock"}:
                    continue
                if _line_is_inside_docstring(lines, idx):
                    continue
                body = _enclosing_function_body(lines, idx)
                out.append((py_file, idx + 1, cls, body))
    return out


def _find_constructor_save_sites(path: Path) -> list[tuple[Path, int, str, str]]:
    """Find ``var = ClassName(...)`` + ``await var.save()`` persist sites.

    Catches the constructor + save gap that bypasses ``Class.create(``.
    """
    assign_re = re.compile(r"\b(\w+)\s*=\s*([A-Z][A-Za-z0-9_]*)\s*\(")
    noise_classes = {
        "Test",
        "AsyncClient",
        "BaseModel",
        "Path",
        "Mock",
        "Dict",
        "List",
        "Set",
        "Tuple",
        "Optional",
        "Type",
        "Union",
        "Any",
        "Field",
        "datetime",
        "timedelta",
        "Decimal",
        "BadRequestError",
        "InsufficientPermissionsError",
        "JVSpatialAPIException",
        "Decision",
        "Subject",
        "Resource",
        "HookMisconfiguredError",
        "PayloadTooLargeError",
    }

    out: list[tuple[Path, int, str, str]] = []
    for py_file in path.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text()
        except Exception:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for m in assign_re.finditer(line):
                var_name, cls = m.group(1), m.group(2)
                if cls in noise_classes:
                    continue
                if _line_is_inside_docstring(lines, idx):
                    continue
                body = _enclosing_function_body(lines, idx)
                save_re = re.compile(rf"\bawait\s+{re.escape(var_name)}\.save\s*\(")
                if not save_re.search(body):
                    continue
                out.append((py_file, idx + 1, cls, body))
    return out


# Per-site exemptions for the AST gate. These are CALL SITES (not classes)
# where ``<Class>.create(...)`` legitimately does not directly wire a
# structural edge but is either (a) a transient draft whose attachment is
# scalar-only by design, or (b) a system-subject policy with no graph
# subject node. Each entry is paired with the follow-up plan that
# reconciles or formally exempts it. The runtime audit (test 5) still
# catches the persisted-orphan side of any of these if they actually
# leak into the live graph as detached rows.
_AST_GATE_SITE_EXEMPTIONS: set[str] = set()
# All per-site exemptions reconciled as of Phase 10.5 close (2026-05-20):
#   - content_profile_atomic_swap.fork_draft:ContentProfile → Plan 10.5-10
#     (HAS_DRAFT_PROFILE).
#   - policy_registry._entry_types_from_manifest:Policy → Plan 10.5-09b
#     (HAS_GOVERNANCE_POLICY).
# Adding a new entry requires a substrate-touching plan that amends
# I-GRAPH-01 in the same commit.


def _site_key(py_file: Path, body: str, cls: str) -> str:
    """Derive ``{filename}:{enclosing_function}:{class}`` key for exemption lookup."""
    func_match = re.search(
        r"^\s*(?:async\s+)?def\s+([a-zA-Z_][\w]*)\s*\(", body, re.MULTILINE
    )
    func_name = func_match.group(1) if func_match else "<module>"
    return f"{py_file.name}:{func_name}:{cls}"


def test_ast_gate_every_create_site_wires_edge():
    """Every Node persist site under ``backend/app/`` wires a structural edge.

    Gated patterns:
      - ``<Class>.create(``
      - ``var = <Class>(...)`` followed by ``await var.save()``

    Each site must include a same-function ``.connect(`` OR a known
    wiring-helper call OR be allowlisted (per
    ``backend/.ci/graph_contiguousness_allowlist.txt``).
    """
    backend_root = Path(__file__).resolve().parents[1]
    app_root = backend_root / "app"
    assert app_root.is_dir(), f"backend/app missing at {app_root}"

    exempt = _gate_exempt_classes()
    known_node_classes = {cls.__name__ for cls in _all_node_classes()}

    connect_re = re.compile(r"\.connect\(")
    helper_re = re.compile(
        r"\b(" + "|".join(re.escape(h) for h in _KNOWN_WIRING_HELPERS) + r")\("
    )

    persist_sites = _find_create_sites(app_root) + _find_constructor_save_sites(
        app_root
    )

    unwired: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for py_file, line_no, cls, body in persist_sites:
        key = (str(py_file), line_no, cls)
        if key in seen:
            continue
        seen.add(key)
        if cls not in known_node_classes:
            continue
        if cls in exempt:
            continue
        if connect_re.search(body) or helper_re.search(body):
            continue
        if _site_key(py_file, body, cls) in _AST_GATE_SITE_EXEMPTIONS:
            continue
        rel = py_file.relative_to(backend_root)
        unwired.append(f"{rel}:{line_no}  {cls}")

    assert not unwired, (
        "Unwired Node persist sites detected (I-GRAPH-01):\n  "
        + "\n  ".join(unwired)
        + "\n\nEither (a) wire a structural edge in the same function via "
        ".connect(...) or a known wiring helper, OR (b) add the class to "
        "backend/.ci/graph_contiguousness_allowlist.txt with a reason."
    )


# ---------------------------------------------------------------------------
# Test 5 — no unknown node classes are orphan on the canonical fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_unknown_node_classes_are_orphan():
    """Phase 10.5 Plan 10.5-09 (closure): the gate is now BLOCKING.

    Boot the canonical fixture, run ``run_audit(raise_on_violation=True)``,
    and require zero unlisted orphans. The allowlist is header-only
    post-Plan-10.5-07 — any new orphan on a future commit raises
    ``GraphContiguousnessViolation`` and CI fails.
    """
    report = await run_audit(raise_on_violation=True)
    assert isinstance(report, AuditReport)
    # raise_on_violation=True ensures report.orphans is empty when we
    # reach this line. The assert is belt-and-suspenders.
    assert report.orphans == {}, (
        "Unlisted orphan classes detected on canonical fixture — every "
        "orphan must be in backend/.ci/graph_contiguousness_allowlist.txt:\n"
        f"  orphans = {report.orphans}\n"
        f"  allowlisted_orphans = {report.allowlisted_orphans}"
    )


# ---------------------------------------------------------------------------
# Test 6 — raise_on_violation surfaces GraphContiguousnessViolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_audit_raises_when_requested_and_unlisted_orphan_exists():
    """Seed an orphan + run with raise_on_violation=True against an
    EMPTY allowlist: ``GraphContiguousnessViolation`` must fire."""
    from app.models.nodes import Notification

    notif = await Notification.create(
        user_id="bare-user-id-for-raise-test",
        type="system",
        content="orphan probe — raise check",
        read=False,
    )
    assert notif.id

    # Point the audit at an EMPTY allowlist by monkey-patching the
    # module-level path constant. Restore after.
    import app.services.graph_reachability as gr

    original = gr.ALLOWLIST_PATH
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("# empty for raise-on-violation test\n")
        empty = Path(tmp.name)
    gr.ALLOWLIST_PATH = empty
    try:
        with pytest.raises(GraphContiguousnessViolation) as exc_info:
            await run_audit(raise_on_violation=True)
        assert "Notification" in str(exc_info.value)
    finally:
        gr.ALLOWLIST_PATH = original
        empty.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 7 — allowlist parser tolerates comments + blank lines
# ---------------------------------------------------------------------------


def test_allowlist_parser_format():
    """Phase 10.5 Plan 10.5-09 (closure): allowlist is HEADER-ONLY.

    All nine in-flight classes from the 2026-05-20 audit (UploadSession,
    ShareLink, Conflict, Approval, AgentConfig, ConversationContext,
    ChannelIdentity, Notification, ContentProfile draft) have been
    reconciled across Plans 10.5-01..10.5-10. The file contains ONLY
    the documentation header. Re-adding an entry mid-flight requires
    a substrate-touching plan that justifies the orphan in CONTEXT and
    amends I-GRAPH-01 in the same commit.
    """
    parsed = _parse_allowlist()
    assert parsed == set(), (
        "Phase 10.5 closure invariant violated — "
        "backend/.ci/graph_contiguousness_allowlist.txt is no longer "
        "header-only:\n"
        f"  parsed = {sorted(parsed)}\n"
        "Either reconcile the new orphan or amend I-GRAPH-01 with a "
        "substrate-touching plan."
    )


def test_walker_class_is_registered():
    """``GraphReachabilityWalker`` instantiates and exposes the expected
    state fields (catch import / Pydantic shape regressions early)."""
    w = GraphReachabilityWalker(visited_ids=set(), visited_by_class={})
    assert w.visited_ids == set()
    assert w.visited_by_class == {}
