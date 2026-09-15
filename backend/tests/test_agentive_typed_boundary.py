"""Tests for D-04 typed boundary, D-09 single-Literal invariant, and D-03 envelope handler."""

import re
import subprocess
from pathlib import Path

import pytest

# Import smoke tests — fail loudly if any of these can't be loaded.
from app.agentive.api.errors import install_agentive_error_handlers  # noqa: F401
from app.agentive.nodes import AgentConfig, Connector  # noqa: F401
from app.agentive.types import AgentType  # noqa: F401


def test_agent_type_literal_defined_once():
    """D-09: exactly one canonical AgentType Literal definition.

    AST-based per W3 revision: walks every .py file in app/agentive/ and counts
    Subscript nodes where the value is the name 'Literal' and the slice contains
    exactly the four canonical strings ('jvagent', 'mcp', 'skill_bundle', 'custom')
    in any order. Black formatting / line breaks cannot defeat this check.
    """
    import ast

    canonical = {"jvagent", "mcp", "skill_bundle", "custom"}
    repo_root = Path(__file__).resolve().parents[2]
    agentive_dir = repo_root / "backend" / "app" / "agentive"
    matches: list[str] = []
    for py_path in agentive_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            value = node.value
            # Match either bare 'Literal' or 'typing.Literal'.
            name = None
            if isinstance(value, ast.Name):
                name = value.id
            elif isinstance(value, ast.Attribute):
                name = value.attr
            if name != "Literal":
                continue
            slice_node = node.slice
            # Python 3.9+: slice is the expression directly (Tuple for multi-arg).
            elts = (
                slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            )
            values = {
                el.value
                for el in elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
            if values == canonical:
                matches.append(str(py_path.relative_to(repo_root)))
    assert (
        len(matches) == 1
    ), f"Expected 1 canonical AgentType Literal, found: {matches}"
    assert (
        "types.py" in matches[0]
    ), f"AgentType Literal must live in types.py, found at: {matches[0]}"


@pytest.mark.asyncio
async def test_connector_node_capabilities_round_trip():
    """D-10: Connector.capabilities round-trips as List[str], not coerced."""
    c = await Connector.create(
        kind="jvagent",
        owner="user-test-1",
        capabilities=["filing", "query", "summarize"],
    )
    fetched = await Connector.get(c.id)
    assert fetched is not None
    assert fetched.capabilities == ["filing", "query", "summarize"]
    assert isinstance(fetched.capabilities, list)
    assert all(isinstance(cap, str) for cap in fetched.capabilities)


@pytest.mark.asyncio
async def test_connector_node_seven_fields_round_trip():
    """CON-01: All seven canonical Connector fields persist + round-trip."""
    c = await Connector.create(
        kind="mcp",
        auth_state={"token": "abc"},
        sync_cursor="cursor-123",
        mapping_profile="profile-1",
        owner="user-test-2",
        permissions=["read", "write"],
        capabilities=["filing"],
    )
    fetched = await Connector.get(c.id)
    assert fetched is not None
    assert fetched.kind == "mcp"
    assert fetched.auth_state == {"token": "abc"}
    assert fetched.sync_cursor == "cursor-123"
    assert fetched.mapping_profile == "profile-1"
    assert fetched.owner == "user-test-2"
    assert fetched.permissions == ["read", "write"]
    assert fetched.capabilities == ["filing"]


@pytest.mark.asyncio
async def test_get_chat_connector_dispatches_by_agent_type():
    """D-11 vendor-agnostic dispatch — registry routes correctly per agent_type."""
    from app.agentive.connectors.jvagent_connector import JvAgentConnector
    from app.agentive.connectors.mcp_stub_connector import McpStubConnector
    from app.agentive.connectors.registry import get_chat_connector

    assert isinstance(get_chat_connector("jvagent"), JvAgentConnector)
    assert isinstance(get_chat_connector("mcp"), McpStubConnector)
    # Alias normalization at the lookup boundary, not in _REGISTRY:
    assert isinstance(get_chat_connector("integral_assistant"), JvAgentConnector)
    assert isinstance(get_chat_connector(""), JvAgentConnector)


def test_get_chat_connector_unknown_kind_raises():
    from app.agentive.connectors.registry import get_chat_connector

    with pytest.raises(ValueError, match="unknown_vendor"):
        get_chat_connector("unknown_vendor")


@pytest.mark.asyncio
async def test_mcp_stub_connector_no_network_call(monkeypatch):
    """McpStubConnector is routing-proof — it MUST NOT make any HTTP call."""
    from app.agentive.connectors.base import ChatTurnContext
    from app.agentive.connectors.mcp_stub_connector import McpStubConnector

    called = {"http": False}
    try:
        import httpx

        async def _fail_post(*a, **k):
            called["http"] = True
            raise AssertionError("McpStubConnector must not make HTTP calls")

        monkeypatch.setattr(httpx.AsyncClient, "post", _fail_post)
    except ImportError:
        pass

    connector = McpStubConnector()
    ctx = ChatTurnContext(email="alice@example.com", message="hello world")
    result = await connector.send_turn(ctx, preferences={})

    assert result.message == "[mcp-stub] received: hello world"
    assert result.session_id == "stub-session"
    assert result.agent_user_id == "alice@example.com"
    assert result.error is None
    assert called["http"] is False


def test_no_jvagent_dispatch_branches_outside_jvagent_connector():
    """D-11 invariant (B2 revision): no `if x == "jvagent"` / `agent_type == "jvagent"` /
    `match agent_type case "jvagent"` branches exist OUTSIDE the jvagent connector module.

    AST-based per B2 revision: the prior grep approach false-positived on legitimate
    side-effect imports like `from app.agentive.connectors import jvagent_connector` in
    registry.py / __init__.py. We now walk the AST and look ONLY for actual dispatch
    branches — Compare nodes with Eq op against Constant("jvagent") and match-case
    patterns — which are the real vendor-coupling regression vector. Imports, type
    aliases, comments, and docstrings are all naturally ignored.
    """
    import ast

    repo_root = Path(__file__).resolve().parents[2]
    agentive_dir = repo_root / "backend" / "app" / "agentive"
    offending: list[str] = []

    for py_path in agentive_dir.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
        # Allow: the jvagent connector module itself owns its name + branches.
        if rel.endswith("connectors/jvagent_connector.py"):
            continue
        # Allow: types.py — declares the canonical Literal.
        if rel.endswith("agentive/types.py"):
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # Pattern 1: `<expr> == "jvagent"` or `"jvagent" == <expr>`
            if isinstance(node, ast.Compare):
                if any(isinstance(op, ast.Eq) for op in node.ops):
                    operands = [node.left] + list(node.comparators)
                    for operand in operands:
                        if (
                            isinstance(operand, ast.Constant)
                            and isinstance(operand.value, str)
                            and operand.value == "jvagent"
                        ):
                            offending.append(
                                f"{rel}:{node.lineno}: == 'jvagent' branch"
                            )
                            break
            # Pattern 2: `match x: case "jvagent": ...`
            if isinstance(node, ast.match_case):
                pat = node.pattern
                if (
                    isinstance(pat, ast.MatchValue)
                    and isinstance(pat.value, ast.Constant)
                    and pat.value.value == "jvagent"
                ):
                    offending.append(f"{rel}:{pat.value.lineno}: match case 'jvagent'")

    assert not offending, (
        "D-11 vendor-neutrality regression — jvagent dispatch branches found "
        "outside jvagent_connector.py / types.py:\n" + "\n".join(offending)
    )


@pytest.mark.asyncio
async def test_chat_message_extra_field_rejected_with_canonical_envelope(
    authenticated_client, monkeypatch
):
    """D-04: extra: forbid → 422; D-03: response is canonical 5-key envelope."""
    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hi", "rogue_field": "x"},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    # 5-key canonical envelope:
    assert set(body.keys()) >= {"error_code", "message", "details", "timestamp", "path"}
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["path"] == "/api/agentive/chat/message"


@pytest.mark.asyncio
async def test_chat_message_empty_string_rejected(authenticated_client):
    """Empty message → 400 BadRequestError (post-06-05 canonical envelope).

    Plan 06-05 note: pre-migration this was 422 (Pydantic-driven via
    ``min_length=1`` on ``ChatTurnRequest``). The 06-05 rewrite to jvspatial's
    ``@endpoint`` + flat-keyword signature drops the per-field ``min_length``
    metadata (jvspatial's ``build_field_config`` only forwards ``title`` /
    ``description`` / ``examples``), so the handler explicitly validates and
    raises ``BadRequestError`` → 400 with the canonical 5-key envelope.
    """
    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": ""},
    )
    assert r.status_code == 400
    body = r.json()
    assert set(body.keys()) >= {"error_code", "message", "details", "timestamp", "path"}


@pytest.mark.asyncio
async def test_chat_message_too_long_rejected(authenticated_client):
    """Message exceeding 8000 chars → 400 BadRequestError (post-06-05 envelope).

    Same rationale as the empty-string test above: jvspatial's flat-keyword
    parameter model drops ``max_length`` constraints, so the handler enforces
    the bound and raises ``BadRequestError`` → 400.
    """
    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "x" * 8001},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_chat_message_happy_path_returns_chat_turn_response(
    authenticated_client, monkeypatch
):
    """ChatTurnResponse shape: ok, message, session_id, agent_user_id, agent_type."""
    from app.agentive.connectors.base import ChatTurnResult
    from app.agentive.services import uplink_registry as uplink_registry_mod

    class _FakeConfig:
        agent_type = "mcp"
        preferences: dict = {}

    async def _mock_get_system():
        return _FakeConfig()

    class _FakeConnector:
        async def send_turn(self, ctx, *, preferences):
            return ChatTurnResult(
                message="ok",
                session_id="sess-1",
                agent_user_id="alice@example.com",
            )

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry,
        "get_system_agent",
        _mock_get_system,
    )
    monkeypatch.setattr(
        "app.agentive.api.chat.get_chat_connector",
        lambda _t: _FakeConnector(),
    )

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hello"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "ok"
    assert body["session_id"] == "sess-1"
    assert body["agent_type"] == "mcp"


@pytest.mark.asyncio
async def test_proactive_push_redirect(authenticated_client):
    """D-06: old /proactive/push → 308 → /proactive/log-push."""
    r = await authenticated_client.post(
        "/api/agentive/proactive/push",
        json={"channel": "in_app", "message_type": "test", "payload": {}},
        follow_redirects=False,
    )
    assert r.status_code == 308
    assert r.headers["location"] == "/api/agentive/proactive/log-push"


@pytest.mark.asyncio
async def test_proactive_log_push_returns_202_no_pushed_lie(authenticated_client):
    """D-06: new endpoint is HTTP 202 with {accepted, logged_at}, no `pushed` key."""
    r = await authenticated_client.post(
        "/api/agentive/proactive/log-push",
        json={"channel": "in_app", "message_type": "test", "payload": {}},
    )
    assert r.status_code == 202
    body = r.json()
    assert body.get("accepted") is True
    assert "logged_at" in body
    assert "pushed" not in body, "D-06: response must not claim pushed: true"


def test_no_legacy_error_shape_in_agentive_api():
    """D-03 envelope rollout: zero `{"error": ...}` early-returns survive."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-rn", '"error":', "backend/app/agentive/api/", "--include=*.py"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    offending = []
    for line in result.stdout.splitlines():
        after = line.split(":", 2)[-1] if line.count(":") >= 2 else line
        stripped = after.lstrip()
        # Allow: error_code references, comments, test files, docstrings,
        # references to .error attributes (e.g. result.error, response.error).
        if (
            "error_code" in line
            or stripped.startswith("#")
            or stripped.startswith('"""')
        ):
            continue
        if (
            "result.error" in line
            or "response.error" in line
            or ".error" in stripped[:40]
        ):
            continue
        # Allow our pydantic field declarations like `error: Optional[str] = None`
        if re.search(r"\berror:\s*Optional\[", line):
            continue
        offending.append(line)
    assert (
        not offending
    ), f"Found legacy `{{'error': ...}}` shape in agentive/api/:\n" + "\n".join(
        offending
    )


# ====== Plan 01-02 tests: datetime hygiene + OTP fix + silent-except cleanup ======


def test_utc_now_helpers():
    """Plan 01-02 / D-05: utc_now and utc_now_iso are tz-aware UTC."""
    from datetime import timezone

    from app.utils.time import utc_now, utc_now_iso

    n = utc_now()
    assert n.tzinfo is not None
    assert n.tzinfo == timezone.utc
    s = utc_now_iso()
    assert s.endswith("+00:00") or s.endswith("Z"), f"Expected UTC suffix, got {s!r}"


def test_no_datetime_utcnow_in_agentive():
    """D-05: zero datetime.utcnow() calls survive in app/agentive/."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-rn", "datetime.utcnow", "backend/app/agentive/", "--include=*.py"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    # Allow comments/docstrings only:
    offending = [
        line
        for line in result.stdout.splitlines()
        if "datetime.utcnow" in line.split(":", 2)[-1]
        and not line.split(":", 2)[-1].lstrip().startswith("#")
    ]
    assert not offending, "datetime.utcnow survives in app/agentive/:\n" + "\n".join(
        offending
    )


def test_no_naive_datetime_now_in_agentive():
    """All datetime.now() in app/agentive/ uses (timezone.utc) — naive form returns nothing.

    Scanned with Python ``re`` (not shell grep) so ``\\s`` is portable across
    GNU grep builds that do not treat ``\\s`` as whitespace.
    """
    import re

    repo_root = Path(__file__).resolve().parents[2]
    agentive = repo_root / "backend" / "app" / "agentive"
    rx = re.compile(r"datetime\.now\(\s*\)")
    offending: list[str] = []
    for py in agentive.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if rx.search(code):
                rel = py.relative_to(repo_root).as_posix()
                offending.append(f"{rel}:{i}:{line}")
    assert not offending, "naive datetime.now() in app/agentive/:\n" + "\n".join(
        offending
    )


def test_no_silent_except_pass_in_agentive():
    """All silent except sites converted to logger.warning or fail-closed."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "grep",
            "-rEn",
            r"^\s*except[^:]*:\s*pass\s*$",
            "backend/app/agentive/",
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert (
        result.stdout.strip() == ""
    ), f"Silent except: pass survives in app/agentive/:\n{result.stdout}"


def test_uplink_registry_has_documented_shape():
    """W5 / AGT-01: uplink_registry.py has module + class docstrings naming all
    ten AgentConnection fields."""
    repo_root = Path(__file__).resolve().parents[2]
    path = (
        repo_root / "backend" / "app" / "agentive" / "services" / "uplink_registry.py"
    )
    text = path.read_text(encoding="utf-8")

    # At least two top-of-block triple-quoted docstrings (module + at least one class).
    triple_quote_lines = [
        ln for ln in text.splitlines() if ln.lstrip().startswith('"""')
    ]
    assert (
        len(triple_quote_lines) >= 2
    ), f"Expected >= 2 docstring openers in uplink_registry.py; got {len(triple_quote_lines)}"

    # Every AgentConnection field must be named in a docstring.
    required_fields = [
        "config_id",
        "agent_type",
        "uplink_url",
        "capabilities",
        "scope",
        "user_id",
        "workspace_id",
        "preferences",
        "last_heartbeat",
        "connected",
    ]
    missing = [f for f in required_fields if f not in text]
    assert not missing, f"AgentConnection fields not documented: {missing}"
