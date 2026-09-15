"""Node definitions for Integral entities.

All entities in Integral are represented as Nodes in the jvspatial graph database.
Entity class names match persisted ``entity`` (no ``Node`` suffix).
"""

from typing import Any, Dict, List, Literal, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index
from pydantic import Field

from app.schemas.provenance import Provenance

# --- Fixed graph singleton ids (app shell) ---
APP_NODE_ID = "n.IntegralApp.integral"
USERS_REGISTRY_ID = "n.Users.integral"
WORKSPACES_REGISTRY_ID = "n.Workspaces.integral"
CONTENT_PROFILES_REGISTRY_ID = "n.ContentProfiles.integral"
INVITATIONS_REGISTRY_ID = "n.Invitations.integral"


class IntegralApp(Node):
    """Singleton application node under Root: settings and metadata.

    Renamed from ``App`` in Phase 10 / Plan 10-01 to free the Python class
    name ``App`` for the user-facing primitive (formerly ``Space``). The
    persisted ``__entity_name__`` discriminator was already ``"IntegralApp"``
    pre-rename, so this rename is Python-only — no DB schema effect on this
    class. See Architectural Decision 1 in
    ``.planning/phases/10-app-bundles-v1-foundation/10-RESEARCH.md`` for
    full analysis.
    """

    # Decouple the persisted entity discriminator from the Python class name.
    # jvagent's framework also defines a class literally named ``App`` (see
    # ``jvagent.core.app.App``) that hangs off Root in the same jvspatial DB
    # when AGENTIVE_ENABLED=1. Without this override, jvspatial's
    # ``find_subclass_by_name(Node, "App")`` non-deterministically returns
    # one class for both — causing jvagent's ``isinstance`` checks to miss
    # its own previously-created App and spawn a fresh duplicate every boot.
    __entity_name__ = "IntegralApp"

    name: str = "Integral"
    settings: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Users(Node):
    """Registry cataloging every User in the deployment."""

    created_at: Optional[str] = None


class ContentProfiles(Node):
    """Registry cataloging library ContentProfile packages."""

    created_at: Optional[str] = None


class Invitations(Node):
    """Registry cataloging every pending/consumed Invitation."""

    created_at: Optional[str] = None


class Workspaces(Node):
    """Registry cataloging every Workspace (Personal + Organization)."""

    created_at: Optional[str] = None


class Apps(Node):
    """Per-workspace registry cataloging Apps inside one Workspace.

    Renamed from ``Spaces`` in Phase 10 / Plan 10-01 — the user-facing
    primitive (formerly ``Space``) is now ``App``. The persisted
    discriminator is explicit (``__entity_name__ = "Apps"``) so the rename
    is self-documenting and survives any future Python class rename.

    One per Workspace, linked via ``Workspace—CONTAINS→Apps`` and keyed
    deterministically off the workspace id so the branch is idempotently
    re-resolvable from any catalog write path.

    No backwards-compat alias is retained — hard cutover per
    ``.planning/migrations/space_to_app_rename.md``. Consumers that still
    import the old ``Spaces`` name will fail; Plan 10-02 (same PR) renames
    them.
    """

    __entity_name__ = "Apps"

    workspace_id: str = ""
    created_at: Optional[str] = None


class Tracks(Node):
    """Per-workspace registry cataloging Tracks inside one Workspace."""

    workspace_id: str = ""
    created_at: Optional[str] = None


class ChatThreads(Node):
    """Per-workspace registry cataloging ChatThreads inside one Workspace."""

    workspace_id: str = ""
    created_at: Optional[str] = None


class Views(Node):
    """Per–content-profile registry cataloging View nodes for a track."""

    track_id: str = ""
    content_profile_id: str = ""
    created_at: Optional[str] = None


class Dashboards(Node):
    """Per-app registry cataloging Dashboard nodes."""

    app_id: str = ""
    workspace_id: str = ""
    created_at: Optional[str] = None


class Dashboard(Node):
    """App-scoped analytics dashboard — a grid of declarative widgets."""

    name: str = ""
    name_fold: str = ""
    app_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    layout: Dict[str, Any] = Field(
        default_factory=lambda: {"columns": 12, "row_height": 80}
    )
    widgets: List[Dict[str, Any]] = Field(default_factory=list)
    is_default: bool = False
    created_by: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ContentProfile(Node):
    """Attached or library content package (entry types, tags, views templates).

    Draft/publish lifecycle (Pillar 2 — agent-authorable substrate):

    - ``status``               — ``"published"`` (canonical) or ``"draft"``.
      Default is ``"published"`` so existing rows keep their semantics.
    - ``published_at``         — set on transition to ``"published"`` via the
      atomic-swap helper.
    - ``draft_of_id``          — when ``status == "draft"``, points back at the
      published CP this draft was forked from.
    - ``published_id``         — when ``status == "published"``, points at the
      most recent draft that was promoted (or empty for the original).
    - ``parent_version_id``    — version-graph parent. Today equal to
      ``published_id`` for published rows; reserved for full version-graph
      branching later.
    - ``version_number``       — monotonic per-lineage counter; bumped on
      publish.
    - ``version_label``        — optional human label ("v1.1", "Q2 cleanup").
    - ``signature``            — opaque audit envelope (proposer, approver,
      tool, etc.) populated by the staging executor on publish.
    """

    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for case-insensitive uniqueness lookup
    version: Optional[str] = None
    manifest: Dict[str, Any] = Field(default_factory=dict)
    scope: str = ""  # platform | organization | community (library)
    # Workspace-scoped CPs carry ``workspace_id``; library packages have none.
    workspace_id: Optional[str] = None
    app_id: str = ""  # owning App when used as track template or App-attached
    library_package: bool = False  # True when cataloged under ContentProfiles registry
    description: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Draft/publish lifecycle (Pillar 2)
    status: str = "published"
    published_at: Optional[str] = None
    draft_of_id: Optional[str] = None
    published_id: Optional[str] = None
    parent_version_id: Optional[str] = None
    version_number: int = 1
    version_label: Optional[str] = None
    signature: Dict[str, Any] = Field(default_factory=dict)
    # Phase 5 Plan 05-01 — CP-level migration rollup (Plan 05-02 populates).
    # Values: "complete" | "in_progress" | "failed".
    migration_status: str = "complete"
    # Phase B (B3) — bundle-derived metadata for library packages.
    # Populated by ``content_profile_library_seed._upsert_one`` from the
    # ``LibraryProfileSpec`` returned by the v3 loader. Carries
    # ``bundle_fingerprint`` (SHA-256 over the bundle file manifest),
    # ``manifest_fingerprint`` (canonical-manifest hash), ``signature_verified``
    # / ``signature_reason`` (verify_bundle_signature outcome), ``ships_python``
    # (declared skill bundles), ``slug`` (package slug), and
    # ``bundle_dir_path`` (filesystem location). Hot-load detects file edits
    # via ``bundle_fingerprint`` drift even when the canonical manifest is
    # unchanged — see ``_upsert_one`` for the trigger logic.
    metadata: Dict[str, Any] = Field(default_factory=dict)


class User(Node):
    """System user profile (AuthUser linked via user_id).

    ``avatar_url`` is the legacy free-form URL (kept for back-compat with
    pasted/imported avatars). ``avatar_attachment_id`` (Phase 9 Plan 09-01,
    AVT-01) points at the canonical 128-pixel Attachment variant produced
    by the resize pipeline; per-size variants are reachable by walking
    ``HAS_ATTACHMENT`` and filtering by the associative-edge ``role`` +
    ``size`` fields (pillar 2 — relationship state on the edge).
    """

    user_id: str = attribute(default="", indexed=True)
    display_name: str = ""
    avatar_url: str = ""
    # Phase 9 Plan 09-01 (AVT-01) — FK to the canonical 128px Attachment.
    # Other variants (32/64/256) are discoverable by walking
    # ``HAS_ATTACHMENT`` and filtering by edge.role=="avatar" + edge.size.
    avatar_attachment_id: Optional[str] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)
    # Phase 9 Plan 09-03a (NOTIF-02) — per-user notification channel
    # preference matrix. None = use defaults from
    # ``app.schemas.notification_preferences.default_preferences``. Shape is
    # ``{channels: {in_app, email, whatsapp: bool}, kinds: {<NotificationKind>:
    # {in_app, email, whatsapp: bool}}, whatsapp: {opted_in_at, phone_e164}}``.
    # Persisted as ``Dict`` on the Node mirrors the Phase 2 ``ChangeEvent``
    # idiom; the Pydantic boundary in
    # ``app/schemas/notification_preferences.py`` enforces the shape.
    notification_preferences: Optional[Dict[str, Any]] = None
    # Per-user dictation preferences for the chat composer mic. None = the
    # defaults in ``app.schemas.speech_preferences.SpeechPreferences``; that
    # boundary enforces the shape. Written only by
    # ``PATCH /users/me/speech-preferences``.
    speech_preferences: Optional[Dict[str, Any]] = None
    # Phase 9 Plan 09-05 (ONBD-01) — onboarding completion timestamp.
    # ``None`` means the user has not yet completed first-login onboarding
    # via the ``integral_onboard_user`` MCP tool. Existing pre-09-05 users
    # are backfilled at server startup so they don't re-onboard
    # (see services/onboarding_backfill.py).
    onboarded_at: Optional[str] = None
    # Server-side source of truth for the user's currently-selected
    # workspace scope. The frontend's ``X-Integral-Scope`` header is an
    # optimistic hint; ``request_scope.resolve_workspace_id_from_request``
    # validates the hint against live membership and falls back to this
    # field on mismatch (workspace deleted, membership revoked, stale
    # client cache after a DB purge, fresh device).
    active_workspace_id: str = ""
    # True iff ``active_workspace_id`` was set by an EXPLICIT user
    # action (header-validated request or ``PUT /users/me/scope``). When
    # False, the resolver treats ``active_workspace_id`` as a stale
    # auto-default and re-picks (e.g. the user gains org membership
    # after first login — next request flips to the org). Without this
    # flag, a one-time bootstrap into Personal would stick forever even
    # after the user becomes part of an org.
    active_workspace_id_explicit: bool = False
    email_verified: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@compound_index([("kind", 1), ("created_at", -1)], name="idx_ws_kind_created")
class Workspace(Node):
    """First-class workspace primitive (Personal | Organization).

    A Workspace is the top-level container — every Space, Track, Entry, and
    chat thread is owned by exactly one Workspace. Visibility cascades from
    Workspace down; cross-workspace traversal is impossible in the data
    model.

    Ownership for both kinds is expressed graph-natively as a
    ``User —IS_MEMBER_OF{role:"owner"}→ Workspace`` edge. Personal
    workspaces have exactly one such edge; Organization workspaces have
    one edge per member with role in {owner, admin, member, guest}.

    ``kind`` discriminator (internal access-model axis):
      - ``personal``     — exactly one per User. No invitation flow, no
                            member pool beyond the owner, not deletable
                            via API. Auto-created on signup.
      - ``organization`` — multi-user; members via ``IS_MEMBER_OF`` edge;
                            invitations target this workspace; storage
                            quota meaningful.

    ``workspace_type`` is the user-facing category label (e.g.
    ``"company"``, ``"personal"``). It does not affect the access model
    or member semantics — those follow ``kind``. ``"personal"`` is
    reserved for the auto-provisioned per-user workspace and is not
    user-assignable via the API.
    """

    kind: str = attribute(
        default="organization", indexed=True
    )  # "personal" | "organization"
    workspace_type: str = "company"  # free-form category, e.g. "company", "personal"
    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for index-friendly lookup
    description: str = ""
    accent_color: str = ""  # #RGB / #RRGGBB; empty = client theme default
    # Free-form URL paste — legacy + still supported for pasted/imported
    # avatars. Mutually exclusive with ``avatar_attachment_id``: when an
    # attachment is uploaded the server overwrites this with a stable
    # ``/api/workspaces/{id}/avatar?size=128&v={updated_at}`` URL so every
    # existing consumer continues to read ``ws.avatar_url`` unchanged.
    avatar_url: str = ""
    # Phase 9 NOTIF/AVT extension — uploaded avatar pipeline (same
    # ``HAS_ATTACHMENT(role='avatar', size=N)`` shape as ``User.avatar_*``).
    # When set, the 128-px variant is canonical and ``avatar_url`` mirrors
    # the served path.
    avatar_attachment_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    storage_bytes_used: int = 0
    storage_quota_bytes: int = 0
    # Phase D / I-WSINIT-03: list of {slug, version, applied_at} entries for
    # workspace-scope profiles applied at strict-init. Append-only history;
    # authoritative read source for "which bundles has this workspace
    # provisioned?" queries. Defaults to an empty list via
    # ``Field(default_factory=list)`` to avoid the shared-mutable-default
    # trap that bites bare ``= []`` class-level defaults.
    applied_profiles: List[Dict[str, Any]] = Field(default_factory=list)


class Invitation(Node):
    """Pending invitation — workspace-level or resource-level.

    Tokens are stored as SHA-256 of a URL-safe random string. Plaintext token
    is returned to the issuer in the response and never logged or persisted.

    Lifecycle:
        pending → accepted / declined / revoked / expired

    Two flavours, distinguished by which fields are populated:

    * **Workspace-level** (legacy + still supported): ``workspace_id`` set,
      ``target_resource_*`` empty. On accept, materializes
      ``IS_MEMBER_OF{role:role}`` between the accepting User and the
      Workspace, with ``can_create_apps`` / ``can_create_tracks`` flags.

    * **Resource-level** (Phase 3): ``target_resource_type`` and
      ``target_resource_id`` set, ``target_resource_role`` carries the
      collaboration role. On accept, materializes
      ``COLLABORATES_ON{role:target_resource_role}`` between the accepting
      User and the resource, plus cross-workspace
      ``IS_MEMBER_OF{role:"guest"}`` if the user isn't a workspace member.
      ``workspace_id`` is set to the resource's workspace for fast lookup.
    """

    # Invitations always carry a workspace_id (the resource's workspace for
    # resource-level invites; the invited workspace for legacy ones).
    workspace_id: str = attribute(default="", indexed=True)
    email: str = attribute(default="", indexed=True)  # casefold-normalized
    invited_user_id: Optional[str] = None  # resolved at create if email maps to a User
    invited_by_user_id: str = ""
    role: str = "member"  # member | admin | guest (workspace-level only)
    can_create_apps: bool = False
    can_create_tracks: bool = False
    # Phase 3 — resource-level invites.
    target_resource_type: str = ""  # "" | "app" | "track" | "entry"
    target_resource_id: str = attribute(default="", indexed=True)
    target_resource_role: str = ""  # owner | admin | editor | commenter | viewer
    token_hash: str = ""
    status: str = "pending"  # pending | accepted | declined | revoked | expired
    message: str = ""
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    consumed_at: Optional[str] = None


class App(Node):
    """Named grouping of tracks; access can cascade to contained tracks.

    Set to ``"WorkspaceApp"`` to avoid collision with jvagent's own ``App``
    node class (also a ``Node`` subclass; its discriminator defaults to the
    class ``__name__`` ``"App"``).  DB rows migrated: ``"Space"`` → ``"App"``
    → ``"WorkspaceApp"``.
    """

    __entity_name__ = "WorkspaceApp"

    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for fuzzy index-friendly lookup
    owner_user_id: Optional[str] = None
    description: str = ""
    visibility: str = "private"  # private, workspace, public
    # Every App lives in exactly one Workspace (Personal or Organization).
    workspace_id: str = attribute(default="", indexed=True)
    attached_content_profile_id: str = ""
    library_merge_source_id: Optional[str] = None
    accent_color: str = ""  # #RGB / #RRGGBB; empty = client theme default
    # Phase 36 — workspace-scoped display order. Set by the
    # PATCH /workspaces/{id}/apps/order endpoint. Lives on the App
    # node (not on an edge) because Apps relate to their Workspace via
    # the apps-branch + CATALOGS edge — not a direct CONTAINS — and
    # the existing list_apps read path already sorts by App fields.
    # Null = unordered (sorts to the end of the workspace's app list).
    position: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Phase D: bundle slug this App was provisioned from (for skill
    # resolution + merge-library refresh). ``None`` when the App was
    # created blank rather than from an App-scope profile manifest.
    source_profile_slug: Optional[str] = None
    # Phase 10 Plan 10-05 — App Bundles v1 lifecycle + settings + provenance.
    # All fields are additive with safe defaults so existing App rows from
    # Plans 10-01..10-04 require no migration.
    #
    # ``settings``: user-submitted values for keys declared in ``settings_schema``.
    # Read at runtime by skills (via ``{{settings.*}}`` templating) and custom
    # handlers (via ``context.app.settings``); written only by the install
    # flow (resume-from-token branch) and the App's Settings page.
    #
    # ``settings_schema``: fast-path mirror of the attached ContentProfile's
    # ``manifest.app.settings_schema`` section. Canonical source remains the
    # manifest — this mirror exists so the install/resume endpoints can render
    # the form without re-compiling the manifest on each call.
    #
    # ``lifecycle_state``: state-machine column per app_bundles_v1.md §9.
    #   - ``installing``        — install transaction in flight; intermediate
    #                              state that should never persist past a
    #                              single API call (transaction either
    #                              advances to awaiting_settings/active or
    #                              compensates back to deletion).
    #   - ``awaiting_settings`` — install paused at step 9; install_token
    #                              issued; reaper compensates past TTL.
    #   - ``active``            — normal operating state.
    #   - ``paused``            — admin-paused; agents disabled; data retained.
    #   - ``uninstalled``       — terminal soft-delete tombstone (archive=True
    #                              uninstall path). Force-uninstall hard-deletes
    #                              the App row, leaving no tombstone here.
    #
    # ``installed_from_library_id``: denormalized provenance — the
    # ``ContentProfile.id`` of the library package this App was installed
    # from. Plan 10-06 uses this to drive update-from-library re-merges.
    #
    # ``installed_at``: ISO 8601 timestamp when ``lifecycle_state`` first
    # transitioned to ``active``. Reaper compares this against
    # ``APP_INSTALL_TOKEN_TTL_HOURS`` for ``awaiting_settings`` rows.
    #
    # ``version``: package version at install time (from the library
    # manifest's ``package.version``). Plan 10-06 uses this for
    # update-from-library version-bump detection.
    settings: Dict[str, Any] = Field(default_factory=dict)
    settings_schema: Dict[str, Any] = Field(default_factory=dict)
    lifecycle_state: str = "active"
    installed_from_library_id: Optional[str] = None
    installed_at: Optional[str] = None
    version: Optional[str] = None
    # F0 — immutable package artifact identity for the installed instance.
    # Distinct from ``version`` (display) and ``installed_from_library_id``
    # (mutable catalog row). Upgrade transitions stamp a new fingerprint.
    installed_package_slug: Optional[str] = None
    installed_package_version: Optional[str] = None
    installed_artifact_fingerprint: Optional[str] = None
    # Phase D (D3) — free-form metadata slot for provisioning workflows.
    # Used by the workspace-scope strict-init service to stash a pending
    # sub-manifest on the App for downstream compilation. Mirrors the
    # established ContentProfile.metadata pattern (additive scalar, no
    # graph wiring required).
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Track(Node):
    """Container for a single-purpose project or area of work."""

    title: str = ""
    title_fold: str = ""  # casefold(title.strip()) for fuzzy index-friendly lookup
    owner_id: Optional[str] = None
    purpose: str = ""
    icon: str = ""
    accent_color: str = ""  # #RGB / #RRGGBB; empty = client theme default
    # Effective visibility resolves via parent App when "inherit" (ARCHITECTURE
    # §9.5). Standalone tracks with inherit and no parent App are private.
    # "private" opts out of workspace-wide visibility inheritance from a
    # parent App; direct App OWNS / COLLABORATES_ON still cascade. Per-user
    # EXCLUDED_FROM also blocks inherited paths.
    visibility: str = "inherit"  # inherit | private | workspace | public
    template_id: Optional[str] = None
    # Every Track lives in exactly one Workspace. Tracks inside an App
    # always carry the parent App's workspace_id (persisted on write for
    # query speed).
    workspace_id: str = attribute(default="", indexed=True)
    attached_content_profile_id: str = ""
    library_merge_source_id: Optional[str] = None
    # Phase 4 (MEM-01) — graph-queryable discriminator. "" = ordinary Track;
    # "agent_scratch" = per-user agent working memory Track (one per user, in
    # personal workspace). Additive field — existing Tracks default to ""; no
    # migration. CONTEXT lock #1 / RESEARCH §Q7 Option A.
    kind: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EntryType(Node):
    """Blueprint for a category of Entries (track-scoped or template under CP)."""

    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for case-insensitive uniqueness lookup
    icon: str = "document"
    form_schema: Dict[str, Any] = Field(default_factory=dict)
    track_id: str = attribute(default="", indexed=True)
    is_template: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@compound_index([("track_id", 1), ("created_at", -1)], name="idx_entry_track_created")
@compound_index([("author_id", 1), ("created_at", -1)], name="idx_entry_author_created")
class Entry(Node):
    """Fundamental unit of content within a Track."""

    type_id: str = ""
    title: str = ""
    author_id: str = attribute(default="", indexed=True)
    track_id: str = attribute(default="", indexed=True)
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    reactions: Dict[str, List[str]] = Field(default_factory=dict)
    body: str = ""
    attachment_ids: List[str] = Field(default_factory=list)
    # Forward-compat slot only. The access resolver does NOT consult this
    # field — entry access follows the parent Track unless a per-user
    # EXCLUDED_FROM edge intervenes.
    visibility: str = "inherit"  # "inherit" | "private" (reserved)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # PROV-01 / D-01: typed provenance, defaulted to human at create time.
    provenance: Provenance = Field(default_factory=Provenance.human_default)
    # Phase 5 Plan 05-01 — idempotency key for connector-synced entries.
    # Empty for human/agent-authored entries; populated by sync runtime
    # (Plan 05-03). Locked decision #6 — additive scalar, no migration.
    # Locked decision §A3 — jvspatial field-index support assumed; fallback to
    # ConnectorRecord Node documented in 05-RESEARCH.md §Q4 if absent.
    idempotency_key: str = ""
    # Phase 5 Plan 05-01 — per-Entry migration tracker (Plan 05-02 consumes).
    # Locked decision §A4 — default "complete" so legacy/pre-migration entries
    # are unaffected. Values: "pending" | "running" | "complete" | "failed".
    migration_status: str = "complete"
    migration_error: Optional[str] = None  # populated on "failed"; last error message


class Comment(Node):
    """Comment on an Entry or another Comment (threaded)."""

    author_id: str = ""
    parent_id: Optional[str] = None
    text: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Attachment(Node):
    """Uploaded file metadata (binary in bulk storage).

    Phase 1+1.5 (file handling): adds queryable metadata, content-hash for
    dedup, scan status for the pluggable virus/malware scanner, derived
    dimensions for images/PDFs, and a preview key for cached renditions
    (e.g. LibreOffice-converted PDF for pptx). ``metadata`` is the
    queryable JSON blob produced by AttachmentMetadataExtractor, shaped
    as ``{"common": {...}, "type_specific": {...}}``. ``extracted_text``
    holds full-text content (capped at 10 MB) for the search index.
    """

    filename: str = ""
    mime_type: str = ""
    size: int = 0
    storage_key: str = ""
    source_type: str = "file"
    external_url: str = ""
    uploaded_by: str = ""
    created_at: Optional[str] = None
    # --- Chat file upload (Slice B) ---
    # "entry" (default, back-compat) = HAS_ATTACHMENT edge from an Entry.
    # "chat" = HAS_ATTACHMENT edge from a ChatThread; not filed to an entry.
    owner_kind: Literal["entry", "chat"] = "entry"
    # --- Phase 1 (hardening) ---
    content_hash: str = ""  # sha256 hex; empty for url/legacy
    scan_status: str = "pending"  # pending | clean | blocked | skipped | failed
    scan_engine: str = ""
    scan_message: str = ""
    # --- Phase 1 (derived) ---
    width: Optional[int] = None  # images
    height: Optional[int] = None  # images
    page_count: Optional[int] = None  # PDFs, docx, pptx
    preview_storage_key: str = ""  # sibling cached preview (e.g. _preview.pdf)
    thumb_storage_key: str = ""  # sibling cached thumbnail (images / first page)
    # --- Phase 1.5 (metadata pipeline) ---
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extracted_text: str = ""  # full-text content, capped at 10 MB
    metadata_status: str = (
        "pending"  # pending | processing | complete | partial | failed
    )
    metadata_extractor_version: int = 0
    metadata_error: str = ""


class UploadSession(Node):
    """In-progress chunked upload (Plan 03 — Phase 6).

    Tracks state for a resumable multi-request upload. Bytes are
    staged under ``uploads/{session_id}/<n>.part`` via the standard
    jvspatial storage facade until the client finalises; on
    ``complete`` the chunks are concatenated to the final attachment
    storage key and this row plus the staged parts are removed.

    Lifecycle:

        pending  — session created, no chunks received yet.
        active   — at least one chunk landed; client may continue.
        complete — finalised into an Attachment; row removed shortly after.
        expired  — TTL elapsed; sweep job clears chunks + node.
        cancelled — client called DELETE /uploads/{id}.

    The running ``content_hash`` is updated chunk-by-chunk so the
    per-entry dedup check can fire at finalize without re-reading the
    bytes.
    """

    entry_id: str = ""
    uploaded_by: str = ""
    filename: str = ""
    mime_type: str = ""
    total_bytes: int = 0
    received_bytes: int = 0
    chunk_size: int = 0
    next_chunk_index: int = 0
    content_hash: str = ""  # SHA-256 hex (running)
    status: str = "pending"  # pending | active | complete | expired | cancelled
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    expires_at: Optional[str] = None
    error: str = ""


class Tag(Node):
    """Label within a Track or App content profile (or template under a ContentProfile)."""

    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for case-insensitive uniqueness lookup
    color: str = "#6B7280"
    track_id: str = attribute(default="", indexed=True)
    app_id: str = attribute(default="", indexed=True)
    group_key: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    parent_tag_id: Optional[str] = attribute(default=None, indexed=True)
    applies_to_entry_types: List[str] = Field(default_factory=list)
    is_template: bool = False
    created_at: Optional[str] = None


class View(Node):
    """Saved display configuration for a Track's entries.

    ``entry_type_keys`` constrains which entry types this view surfaces — it
    is the slug list (manifest-stable keys, not raw EntryType node ids) so
    that re-materialization from a content-profile manifest preserves the
    constraint. Empty list = no constraint (show all entry types in track).

    ``default_entry_type_key`` is the slug pre-selected when the user
    triggers "New entry" from within this view. Empty = fall back to the
    track-level default entry type.
    """

    name: str = ""
    name_fold: str = ""  # casefold(name.strip()) for case-insensitive uniqueness lookup
    type: str = "feed"
    config: Dict[str, Any] = Field(default_factory=dict)
    track_id: str = attribute(default="", indexed=True)
    content_profile_id: str = ""
    entry_type_keys: List[str] = Field(default_factory=list)
    default_entry_type_key: str = ""
    is_template: bool = False
    is_default: bool = False
    hidden: bool = False
    created_by: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ShareLink(Node):
    """Tokenized share URL for an App, Track, or Entry.

    Owner mints a ShareLink with a chosen role + optional expiry. The
    plaintext token is returned once at creation; only the SHA-256 hash is
    persisted. Anyone with the URL who is signed in to Integral may redeem
    the link, which materializes ``COLLABORATES_ON{role}`` from User → the
    target resource and (cross-workspace) ``IS_MEMBER_OF{role:"guest"}`` on
    the resource's workspace. Idempotent on re-open.

    Anonymous read access is intentionally NOT supported (signed-in only).

    ``intent`` discriminates how the token may be consumed:

    * ``collaborator`` (default) — signed-in redeem via ``POST /shares/redeem``.
    * ``public`` — unauthenticated read via ``GET /public-share/{token}`` only.
    """

    resource_type: str = ""  # "app" | "track" | "entry"
    resource_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    role: str = "viewer"  # admin | editor | commenter | viewer (not owner)
    intent: str = "collaborator"  # "collaborator" | "public"
    public_permissions: Dict[str, bool] = Field(default_factory=dict)
    public_token: str = ""
    token_hash: str = attribute(
        default="",
        indexed=True,
        index_unique=True,
        index_partial_filter_expression={"context.token_hash": {"$gt": ""}},
    )
    created_by: str = ""
    created_at: Optional[str] = None
    expires_at: Optional[str] = None  # ISO datetime; null = never expires
    revoked_at: Optional[str] = None  # set on revoke
    redemptions: int = 0  # incremented on each successful redeem
    # 0 = unlimited (legacy). Collaborator links default to a finite cap at mint.
    max_redemptions: int = 0


@compound_index([("user_id", 1), ("read", 1)], name="idx_notif_user_read")
class Notification(Node):
    """User notification."""

    user_id: str = attribute(default="", indexed=True)
    type: str = "system"
    content: str = ""
    read: bool = False
    action_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class ChatThread(Node):
    """An AI chat conversation owned by a user.

    Maps 1:1 to a provider-side conversation (e.g. jvagent Conversation via
    ``provider_session_id``). User → ChatThread via ``OWNS``;
    ChatThread → ChatMessage via ``CONTAINS``.
    """

    user_id: str = ""
    # Active workspace at thread creation. Empty for pre-migration rows;
    # backfilled by scripts/migrate_to_workspaces.py to the user's Personal.
    workspace_id: str = ""
    provider_id: str = ""
    # Sticky-at-create agent binding. Resolved by the agent-switcher
    # resolution chain (Task 5) from the request, the user's active
    # ``HAS_AGENT_PREFERENCE`` for the workspace, and provider defaults.
    # Required (non-empty) for new threads on multi-agent providers;
    # pre-release dev DBs may carry rows with the empty default.
    agent_id: str = ""
    provider_session_id: Optional[str] = None
    title: str = ""
    archived: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_message_at: Optional[str] = None
    # Set by integral_propose_design; read+cleared by the commit_batch
    # greenfield-scaffold gate. Shape: {proposed_at_user_turn: int,
    # summary: str, proposed_at: str-iso}. None = no live proposal.
    design_proposed: Optional[Dict[str, Any]] = None
    # Legacy single-slot ask_user marker. Superseded by ``prompt_queue``
    # (Prompt Sheet). Kept nullable for old rows; writers clear it to None.
    pending_question: Optional[Dict[str, Any]] = None
    # Durable Prompt Sheet queue. Shape: {status: open|closed, opened_at,
    # closed_at, close_reason, items: PromptItem[]}. See
    # docs/superpowers/specs/2026-09-08-prompt-sheet-design.md.
    prompt_queue: Optional[Dict[str, Any]] = None


class ChatMessage(Node):
    """A single turn in a ChatThread.

    ``parts`` mirrors the assistant-ui content-part union (text / reasoning /
    tool-call / source / file / image / data) so round-tripping the
    transcript is lossless.

    Containment: a ChatMessage belongs to exactly one ChatThread, addressed
    via a ``CONTAINS`` edge (ChatThread → ChatMessage). All thread→message
    traversal MUST go through the edge; ``thread_id`` below is a
    denormalized cache, not the source of truth.
    """

    # deviation: thread_id is a denormalized cache of the CONTAINS edge's
    # source so ``message_to_dict`` (app/services/chat_threads.py:221) — read
    # once per message in every ``GET /chat/threads/{id}`` response — stays
    # O(1) without re-traversing the edge. Source of truth is the CONTAINS
    # edge created by ``chat_threads.append_message``; this scalar is
    # refreshed on the same write. Per CLAUDE.md "Pragmatism clause" —
    # cached counter / pointer mirroring edge-side source-of-truth is an
    # allowed deviation when refreshed transactionally.
    thread_id: str = ""
    role: str = "user"  # user | assistant | system | tool
    parts: List[Dict[str, Any]] = Field(default_factory=list)
    parent_id: Optional[str] = None
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


# ChangeEvent is not a prime-graph Node — it is persisted as a ``DBLog`` row
# with ``log_level="CHANGE_EVENT"`` in the logging database via
# ``app/services/change_event_logger.py``. ``DBLog`` itself is declared
# ``class DBLog(Object)`` in jvspatial (``jvspatial/logging/models.py``), so
# ChangeEvent persistence already conforms to I-GRAPH-02 — the canonical
# ``jvspatial.core.Object`` subclass is in use today. The runtime envelope is
# ``ChangeEventEnvelope`` (dataclass) in the same module.


class Policy(Node):
    """Authorization policy attached to a subject Node (POL-02).

    Per CONTEXT D-02: lives in core ``models/nodes.py`` (NOT ``app/agentive/``) —
    authorization is core infrastructure available without ``AGENTIVE_ENABLED``.
    Mirrors Phase 2 D-12 reasoning (ChangeEvent persistence in core).

    Per CONTEXT D-04 (LOCKED — DO NOT add/remove/rename fields):

      subject_kind: str           ("human" | "agent" | "connector" | "system")
                                  Pydantic boundary in ``app/schemas/policy.py``
                                  enforces the ActorKind Literal. Persisted as
                                  ``str`` on the Node mirrors Phase 2's
                                  ``ChangeEvent.actor_kind: str = "system"`` idiom
                                  to avoid a circular import on
                                  ``app.schemas.provenance.ActorKind``.
      subject_id: str
      scope: str                  ("track:<id>" | "app:<id>" |
                                   "user:<id>" | "org:<id>" | "*")
      actions: List[str]          (subset of ``PolicyAction`` Literal + ``"*"``
                                   + ``"<resource>.*"`` wildcards; empty = no
                                   actions, fail-closed at evaluate-time)
      entry_types: List[str]      (filter; empty = match any entry_type)
      tags: List[str]             (filter; empty = match any tags)
      requires_human_approval: bool  Phase 7 UX-03 hint —
                                   SHIP-FLAG, NOT enforced in Phase 3.
      is_active: bool             False = ignored at evaluate-time.

    Audit fields:
      created_at / updated_at — ISO 8601 strings stamped by ``policy_registry``.
      created_by              — User.id of the authenticated principal that
                                created the Policy (server-derived).

    Discovery: agents / connectors / users connect to attached Policies via the
    ``HAS_POLICY`` edge (see ``app/models/edges.py``). The
    ``policy_engine.evaluate`` agent/connector branch walks
    ``subject_node.nodes(edge=['HAS_POLICY'], direction='out', node=['Policy'])``
    to enumerate them. Absence of any HAS_POLICY edge means "no access".
    """

    # NOTE: ``subject_kind`` stored as ``str`` on the Node — same idiom as
    # Phase 2's ``ChangeEvent.actor_kind: str = "system"``. The Pydantic
    # boundary in ``app/schemas/policy.py`` (PolicyCreate / PolicyUpdate /
    # PolicyResponse) enforces the ActorKind Literal.
    subject_kind: str = "agent"
    subject_id: str = ""

    scope: str = "*"
    actions: List[str] = Field(default_factory=list)
    entry_types: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    requires_human_approval: bool = (
        False  # Phase 7 UX-03 hint — NOT enforced in Phase 3
    )
    is_active: bool = True

    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


class Conflict(Node):
    """Phase 5 Plan 05-03 — detected divergence between a local edit and
    an external re-sync.

    Created when a connector's ``conflict_policy == "manual_resolve"`` and
    the re-sync runtime finds local changes since ``connector.last_synced_at``.
    Locked decision #7 + RESEARCH §Q5 — locked 9-field shape. v1 ships
    records + REST surface only (UI deferred to Phase 7).

    Registered in ``main.py:node_types`` ALWAYS-ON (core per locked decision
    #12; NOT gated on AGENTIVE_ENABLED).
    """

    connector_id: str = ""
    entry_id: str = ""
    local_snapshot: Dict[str, Any] = Field(default_factory=dict)
    external_snapshot: Dict[str, Any] = Field(default_factory=dict)
    detected_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution: str = ""  # "kept_local" | "applied_external" | "merged"
    resolved_by: Optional[str] = None  # User.id of resolver
    status: str = "open"  # "open" | "resolved"


class Approval(Node):
    """Phase 7 Plan 07-04 — captures an agent write deferred for human approval.

    Created by ``policy_engine.evaluate`` when a matched Policy has
    ``requires_human_approval=True`` AND the action is a write AND the
    subject is an agent. The agent caller receives a 202 Accepted with
    ``approval_id`` + ``expires_at`` and stops retrying.

    9-field shape (mirrors the Phase 5 ``Conflict`` Node + REST surface
    idiom). v1 supports auto-approve re-run for EXACTLY 6 actions:
    entry.create / .update / .delete + track.create + app.create +
    comment.create. Other actions persist an Approval but ``approve``
    returns 422 — the human must manually re-run via the original tool.

    Registered in ``main.py:node_types`` ALWAYS-ON.
    """

    actor_kind: str = "agent"  # ActorKind member (enforced at Pydantic boundary)
    actor_id: str = ""
    action: str = ""  # PolicyAction member (enforced at Pydantic boundary)
    resource_kind: str = ""
    resource_id: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    policy_id: str = ""
    created_at: str = ""
    expires_at: str = ""
    status: str = "pending"  # Literal['pending','approved','rejected','expired']
    decided_at: Optional[str] = None
    decider_id: Optional[str] = None


class Skill(Node):
    """App-bundled Skill (declarative or custom) — Phase 10 / Plan 10-04.

    Closes APP-SKILLS-01. A Skill is a manifest-declared unit of agent capability
    registered when an App is installed under a Workspace. Two kinds exist:

    - ``declarative``: an LLM prompt template that the agent runtime invokes
      through the standard MCP tool surface (``tools_required`` lists the MCP
      tools the prompt is permitted to call).
    - ``custom``: a Python handler reference (``handler_ref`` dotted path) that
      executes server-side. Custom-kind skills are forbidden in the public
      catalog (Architectural Decision 6) — both ``compile_canonical_manifest``
      (Plan 10-03) and ``merge_library_manifest_into_content_profile``
      (Plan 10-04) reject them when ``is_public_catalog`` / a
      ``publisher_tier == "public_catalog"`` package is in play.

    Connected to the owning App via ``App —CONTAINS→ Skill``. The persisted
    discriminator is set explicitly (``__entity_name__ = "Skill"``) per
    Pitfall 10 in ``10-RESEARCH.md``.

    Resolver-time ``private`` enforcement (Architectural Decision 5):
    ``private=True`` skills are only resolvable from their declaring App's
    context. The single gate lives in
    ``app.agentive.services.skill_registry.get_callable_skills`` — see Plan
    10-04 § "Resolver-time `private:` enforcement".

    See ``docs/backend/app-bundles-v1.md`` §6 (Skills full reference) +
    ``§11`` (Custom skills + trust tiers).
    """

    __entity_name__ = "Skill"

    # Owning App + workspace (denormalized for fast scope filters; matches
    # the App's workspace_id at registration time).
    app_id: str = ""
    workspace_id: str = ""

    # Manifest-declared key, unique per App.
    key: str = ""
    name: str = ""
    description: str = ""

    # Kind discriminator — declarative LLM prompt vs custom Python handler.
    kind: Literal["declarative", "custom"] = "declarative"

    # Declarative-skill body. Path inside the App bundle (resolved by the
    # install lifecycle in Plan 10-05). Required when ``kind == declarative``.
    prompt_template_ref: Optional[str] = None

    # Custom-skill body. Python dotted module path to the handler function.
    # Required when ``kind == custom``.
    handler_ref: Optional[str] = None

    # MCP tool names the skill is permitted to call. Validated against the
    # live ``mcp_adapter.build_tool_catalogue()`` result at registration time.
    tools_required: List[str] = Field(default_factory=list)

    # JSON Schema dict describing the skill's invocation parameters.
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)

    # Output descriptor list — see app_bundles_v1.md §5.2.
    outputs: List[Dict[str, Any]] = Field(default_factory=list)

    # Resolver-time gate. private=True skills only resolvable from the
    # declaring App's caller context (Architectural Decision 5).
    private: bool = False

    # Trust tier. ``untrusted`` is the safe default; trusted-partner /
    # first-party Apps may declare ``trusted`` for elevated permissions.
    trust_tier: Literal["untrusted", "trusted"] = "untrusted"

    # External host declarations captured but NOT enforced at install time
    # (Open Question 5 deferred — sandbox/allowlist enforcement is post-v1).
    # Surfaced via API for the install-time capability prompt (Plan 10-05 UI).
    external_apis: List[str] = Field(default_factory=list)

    # Workspace editor overlay (skills-editor v1).
    body_override: Optional[str] = None
    enabled: bool = True
    origin: Literal["bundle", "workspace"] = "bundle"
    customized_at: Optional[str] = None
    customized_by: Optional[str] = None
    stale_default: bool = False
    bundle_default_digest: Optional[str] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None
