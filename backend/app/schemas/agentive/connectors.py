"""Schemas for agentive/api/connectors.py — Connector CRUD bodies (CON-01).

Phase 8 Plan 08-02 — extends the Phase 1 baseline with:

- ``UpdateConnectorRequest`` — partial-update body for the new PATCH route.
  ``owner`` and ``kind`` are intentionally absent (immutable) and
  ``extra: forbid`` blocks any forged injection.
- ``ConnectorListResponse`` — wire shape for the new LIST route.
- Additional Phase-5 scalar fields surfaced on ``ConnectorResponse``:
  ``subclass_slug``, ``sync_interval_seconds``, ``last_synced_at``.
- Derived read-only ``conflict_policy`` field on ``ConnectorResponse``
  resolved from the SyncConnector subclass registry at serialization
  time (per A5 — never persisted on the Connector Node itself).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.agentive.types import AgentType

# ADR-009 §6 — secrets never leave the server on wire responses.
#
# This is an ALLOWLIST, not a denylist. It used to be the latter — it redacted
# ``env`` / ``headers`` / ``oauth`` and passed EVERYTHING else through, which
# is strictly weaker than the generic ``redact_auth_state`` below. Catalog
# installs write collected secrets to the TOP LEVEL of ``auth_state``, so a
# ``client_secret`` came back in full on every create / GET / LIST / PATCH of
# an MCP connector. A denylist here can only ever be as good as the last
# person who remembered to extend it; an allowlist fails closed on the next
# field somebody adds.
_MCP_AUTH_STATE_ALLOWED_KEYS: frozenset = frozenset(
    {
        "url",
        "transport",
        "display_name",
        "catalog_slug",
        "command",
        "args",
        "discovered_tools",
        "health",
        "registry_name",
        "registry_version",
        "environment",
        "reauth_required",
    }
)
# Rendered as ``{key: "[redacted]"}`` — the operator UI shows WHICH variables
# are configured without their values.
_MCP_AUTH_STATE_KEY_ONLY_MAPS: frozenset = frozenset({"env", "headers", "env_defaults"})


#: The placeholder written in place of a credential on every read path. It is
#: never a legitimate value to WRITE: seeing it in an inbound ``auth_state``
#: means a client read the redacted view and echoed it back, which would
#: overwrite the real credential with the string "[redacted]".
REDACTION_MARKER = "[redacted]"


def contains_redaction_marker(value: Any) -> bool:
    """True when *value* carries a redaction placeholder anywhere inside it.

    Used to refuse a round-tripped read on the write path. The connector Edit
    dialog prefills its ``auth_state`` textarea from ``GET /connectors/{id}``,
    which is redacted, and PATCH replaces ``auth_state`` wholesale — so
    opening Edit and pressing Save wrote ``{"headers": {"Authorization":
    "[redacted]"}}`` over a live OAuth token and reported success. The user
    found out on the next tool call. Refusing here holds for every client.
    """
    if isinstance(value, str):
        return value == REDACTION_MARKER
    if isinstance(value, dict):
        return any(contains_redaction_marker(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_redaction_marker(v) for v in value)
    return False


def mcp_safe_auth_state(auth_state: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-visible slice of an MCP connector's ``auth_state`` (ADR-009 §6)."""
    allowed: Dict[str, Any] = {}
    marked: Dict[str, Any] = {}
    for k, v in (auth_state or {}).items():
        if k in _MCP_AUTH_STATE_KEY_ONLY_MAPS:
            # Names only, no values — an operator needs to see WHICH variables
            # are set. These skip the recursive redactor below, which would
            # otherwise delete the ``[redacted]`` marker for a variable whose
            # NAME matches the secret predicate (``MCP_SECRET``).
            if isinstance(v, dict):
                marked[k] = dict.fromkeys(v.keys(), REDACTION_MARKER)
            else:
                marked[k] = REDACTION_MARKER
        elif k == "oauth" and isinstance(v, dict):
            status = v.get("status")
            marked[k] = {"status": status} if status else {}
        elif k in _MCP_AUTH_STATE_ALLOWED_KEYS:
            allowed[k] = v
    # Belt and braces: an allowlisted key whose VALUE nests credential material
    # (a future ``health: {last_error: "...token..."}``) still gets scrubbed.
    out = redact_auth_state(allowed)
    out.update(marked)
    return out


class CreateConnectorRequest(BaseModel):
    """All seven Connector fields per CON-01 + D-10 (capabilities).

    NOTE: ``owner`` is intentionally NOT on this body — it derives from the
    authenticated principal at request time (D-07; T-01-04-01 spoofing
    mitigation). ``extra: forbid`` ensures clients cannot inject unrecognized
    fields, including a forged ``owner``.
    """

    kind: AgentType = "jvagent"
    auth_state: Dict[str, Any] = Field(default_factory=dict)
    sync_cursor: Optional[str] = None
    mapping_profile: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class UpdateConnectorRequest(BaseModel):
    """PATCH body — partial update. ``kind`` and ``owner`` intentionally absent.

    Per T-08-02-S01: ``extra: forbid`` blocks any client-supplied ``owner`` /
    ``kind`` injection. ``mapping_profile`` is kept for back-compat but
    deprecated for binding (I-CON-02 — use IS_CONNECTED_TO edge instead).
    """

    subclass_slug: Optional[str] = None
    sync_interval_seconds: Optional[int] = None
    auth_state: Optional[Dict[str, Any]] = None
    mapping_profile: Optional[str] = None
    permissions: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None
    # Operator label rename (owner-only; never touches credential material).
    label: Optional[str] = None

    model_config = {"extra": "forbid"}


# Keys in ``Connector.auth_state`` that are credential material and must never
# leave the server on a wire response. Exact keys plus the ``_token`` /
# ``_secret`` suffix families (``access_token``, ``refresh_token``,
# ``client_secret``, ``webhook_secret``, …). The per-connector helpers
# (``gmail_safe_auth_state`` / ``quickbooks_safe_auth_state``) additionally
# drop expiry timestamps; this generic redactor is the floor every connector
# response passes through.
# ``authorization`` / ``code_verifier`` / ``x-api-key`` match none of the
# suffix families and were therefore stored in the clear AND echoed back by
# ``GET /connectors/{id}`` — this predicate drives both at-rest encryption and
# wire redaction, so a gap here is a double exposure. A bearer header and a
# PKCE verifier are credential material.
_AUTH_STATE_SECRET_KEYS: frozenset = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "api_key",
        "apikey",
        "x-api-key",
        "password",
        "authorization",
        "code_verifier",
        "pat",
    }
)
_AUTH_STATE_SECRET_SUFFIXES = (
    "_token",
    "_secret",
    "_key",
    "_pat",
    "_password",
    "_credential",
)


def is_auth_state_secret_key(key: str) -> bool:
    """True when ``key`` names credential material inside ``auth_state``."""
    lowered = str(key).lower()
    return lowered in _AUTH_STATE_SECRET_KEYS or lowered.endswith(
        _AUTH_STATE_SECRET_SUFFIXES
    )


def _redact_value(value: Any) -> Any:
    """Recurse into dicts / lists so a nested secret is dropped too."""
    if isinstance(value, dict):
        return {
            k: _redact_value(v)
            for k, v in value.items()
            if not is_auth_state_secret_key(k)
        }
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def redact_auth_state(auth_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of ``auth_state`` with every secret-bearing key dropped.

    Recurses: the flat version missed ``oauth.tokens.access_token`` and
    ``env.QUICKBOOKS_CLIENT_SECRET`` entirely, because only top-level keys
    were tested against the predicate.
    """
    return _redact_value(dict(auth_state or {}))


class ConnectorResponse(BaseModel):
    """Outbound shape — mirrors the Connector node fields plus id + timestamps
    + Phase-5 scalars + Phase-8 derived ``conflict_policy`` field.

    ``agent_type``-style enum (``kind``) is echoed as the canonical D-09 Literal
    so a Phase 2 audit-log consumer can rely on it without re-parsing.

    ``conflict_policy`` is DERIVED read-only from the SyncConnector subclass
    registry at serialization time — it is NOT persisted on the Connector
    Node (per A5 of Phase 8 planning context). ``None`` when no
    ``subclass_slug`` is set or the slug is unregistered.
    """

    id: str
    kind: AgentType
    owner: str
    auth_state: Dict[str, Any]
    sync_cursor: Optional[str]
    mapping_profile: Optional[str]
    permissions: List[str]
    capabilities: List[str]
    conflict_policy: Optional[str] = None
    subclass_slug: Optional[str] = None
    sync_interval_seconds: int = 300
    last_synced_at: Optional[str] = None
    # Connector scoping: "per_user" (owner-only invoke) or "shared"
    # (workspace-member invoke, admin-installed). Plus operator label.
    connection_mode: str = "per_user"
    label: str = ""
    # ADR-009 MCP client mount fields (empty / unknown for sync connectors).
    workspace_id: Optional[str] = None
    health_status: Optional[str] = None
    last_error: Optional[str] = None
    last_health_at: Optional[str] = None
    created_at: Optional[str]
    updated_at: Optional[str]

    model_config = {"extra": "forbid"}


class ConnectorListResponse(BaseModel):
    """LIST response — owner-scoped + per-record policy-filtered.

    Per T-08-02-I02: the LIST endpoint silently drops policy-denied rows
    from BOTH the ``connectors`` array AND the ``total`` field — no
    ``total: N (M hidden)`` leak (mirrors T-06-02-01 idiom).
    """

    connectors: List[ConnectorResponse]
    total: int

    model_config = {"extra": "forbid"}


class MountMcpConnectorRequest(BaseModel):
    """ADR-009 — mount an external MCP server as a workspace connector."""

    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    display_name: Optional[str] = None
    # Official MCP Registry provenance (non-secret metadata).
    registry_name: Optional[str] = None
    registry_version: Optional[str] = None

    model_config = {"extra": "forbid"}


class McpMountResponse(BaseModel):
    """Mount result — ``auth_required`` when the remote MCP server needs OAuth."""

    status: Literal["mounted", "auth_required"]
    connector: ConnectorResponse
    authorization_url: Optional[str] = None

    model_config = {"extra": "forbid"}


class McpOAuthCallbackRequest(BaseModel):
    """Authorization-code payload from the MCP OAuth popup."""

    code: str
    state: str

    model_config = {"extra": "forbid"}


InstallTier = Literal["direct_http", "http_with_auth", "stdio_package", "unsupported"]


class McpRegistryAuthPrompt(BaseModel):
    """Auth header or env var prompt from registry server.json."""

    name: str
    description: str = ""
    required: bool = True
    is_secret: bool = False

    model_config = {"extra": "forbid"}


class McpRegistryManualRecipe(BaseModel):
    """Suggested stdio install recipe for manual or gated auto-install."""

    transport: str = "stdio"
    command: str = ""
    args: List[str] = Field(default_factory=list)
    package_registry: Optional[str] = None
    package_identifier: Optional[str] = None
    package_version: Optional[str] = None
    note: Optional[str] = None

    model_config = {"extra": "forbid"}


class McpRegistryEntry(BaseModel):
    """Normalized official MCP Registry server entry."""

    name: str
    title: str
    description: str = ""
    version: str = ""
    install_tier: InstallTier
    install_allowed: bool = True
    is_latest: bool = False
    remote_url: Optional[str] = None
    repository_url: Optional[str] = None
    auth_header_prompts: List[McpRegistryAuthPrompt] = Field(default_factory=list)
    env_var_prompts: List[McpRegistryAuthPrompt] = Field(default_factory=list)
    manual_recipe: Optional[McpRegistryManualRecipe] = None

    model_config = {"extra": "forbid"}


class McpRegistrySearchResponse(BaseModel):
    """Browse/search response from official MCP Registry proxy."""

    entries: List[McpRegistryEntry]
    next_cursor: Optional[str] = None
    count: int = 0

    model_config = {"extra": "forbid"}


class McpRegistryMountPreview(BaseModel):
    """Mount preview for a registry entry (no secret values)."""

    registry_name: str
    registry_version: str = ""
    install_tier: InstallTier
    display_name: str
    transport: Optional[Literal["stdio", "streamable_http"]] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    auth_header_prompts: List[McpRegistryAuthPrompt] = Field(default_factory=list)
    env_var_prompts: List[McpRegistryAuthPrompt] = Field(default_factory=list)
    manual_recipe: Optional[McpRegistryManualRecipe] = None
    can_auto_mount: bool = False

    model_config = {"extra": "forbid"}


class MountMcpFromRegistryRequest(BaseModel):
    """Mount a registry entry into the active workspace."""

    registry_name: str
    secrets: Dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class McpConnectorHealthResponse(BaseModel):
    """ADR-009 — health probe result for an MCP connector."""

    connector_id: str
    status: str
    last_error: Optional[str] = None
    last_health_at: Optional[str] = None
    tool_count: int = 0

    model_config = {"extra": "forbid"}


class ConnectorToolInfo(BaseModel):
    """One workspace-registered tool for a connector (no secrets).

    ``scope`` is ``canonical`` for slug-addressed keys (the advertised
    surface) or ``row`` for connector-id-addressed legacy keys.
    ``write`` is True when invoking needs a human bless.
    """

    key: str
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    scope: Literal["canonical", "row"] = "row"
    write: bool = False

    model_config = {"extra": "forbid"}


class ConnectorToolsResponse(BaseModel):
    """Tool list for the connector inspector (native + MCP rows)."""

    connector_id: str
    tools: List[ConnectorToolInfo] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class CatalogAuthField(BaseModel):
    """Non-secret description of a catalog auth prompt."""

    name: str
    label: str
    secret: bool = False
    required: bool = True
    control: Literal["text", "toggle"] = "text"
    default: Optional[str] = None
    hint: Optional[str] = None
    # Advanced fields hide behind the install sheet's Advanced Options
    # toggle (e.g. QuickBooks environment selector).
    advanced: bool = False

    model_config = {"extra": "forbid"}


class CatalogAuth(BaseModel):
    type: Literal["oauth2", "api_key", "env", "none", "headers"]
    fields: List[CatalogAuthField] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class CatalogEntry(BaseModel):
    """Vetted in-repo connector package (ADR-010 catalog)."""

    slug: str
    display_name: str
    description: str = ""
    category: Literal["native", "mcp_server", "mcp_package"]
    kind: Literal["sync", "mcp"]
    icon: str
    vetted: bool = True
    auth: CatalogAuth
    transport: Optional[Literal["stdio", "streamable_http"]] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    # Visibility gate: hidden entries are excluded from the catalog LIST
    # and refuse new installs (410), but stay resolvable by slug so
    # existing mounts keep working. ``deprecated_in_favor_of`` names the
    # successor native slug when the entry is deprecated (not merely hidden).
    hidden: bool = False
    deprecated_in_favor_of: Optional[str] = None
    # Auth field names the platform already provides via server env, so the
    # install sheet can hide them behind Advanced Options. Names only —
    # never values.
    platform_configured: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class CatalogListResponse(BaseModel):
    entries: List[CatalogEntry]
    total: int

    model_config = {"extra": "forbid"}


class CatalogInstallRequest(BaseModel):
    secrets: Dict[str, str] = Field(default_factory=dict)
    # Operator display label for the new row (falls back to catalog name).
    label: Optional[str] = None
    # "per_user" (default): only the installer invokes through this row.
    # "shared": every workspace member invokes through it (admin-only).
    connection_mode: Literal["per_user", "shared"] = "per_user"

    model_config = {"extra": "forbid"}


class McpReauthorizeResponse(BaseModel):
    """Restart-OAuth result for an already-mounted MCP connector.

    Distinct from ``CatalogInstallResponse`` because nothing is created — the
    caller opens ``consent_url`` and the existing connector is re-authorized in
    place, keeping its id, workspace edge and tool registrations.
    """

    connector_id: str
    consent_url: str
    state: str

    model_config = {"extra": "forbid"}


class CatalogInstallResponse(BaseModel):
    """oauth → open consent_url; created → Connector already persisted."""

    action: Literal["oauth", "created"]
    slug: str
    consent_url: Optional[str] = None
    state: Optional[str] = None
    connector: Optional[ConnectorResponse] = None

    model_config = {"extra": "forbid"}
