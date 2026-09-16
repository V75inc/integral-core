"""Application configuration (Pydantic Settings on top of jvspatial env).

jvspatial reads ``JVSPATIAL_*`` env vars natively via
``jvspatial.env_adapter.server_config_overrides_from_env`` — host, port,
CORS, auth, db, file storage etc. all flow through that path. This
module only declares settings consumed by *integral* app code (auth
service, websocket auth, password reset, etc.). Anything jvspatial
already reads from the environment is not duplicated here; the legacy
→ canonical name bridge lives in ``app.main`` and runs before
``Server()`` construction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env locations from THIS file's path, not the process CWD.
# config.py lives at <repo>/backend/app/config.py, so:
#   parents[1] → backend/   (backend-local .env)
#   parents[2] → <repo>/    (shared root .env, e.g. RATE_LIMIT_DISABLED)
# CWD-relative loading (the old ``load_dotenv("../.env")``) only worked
# when the server was launched from backend/; under Docker / uvicorn /
# repo-root launches it silently missed the root .env, leaving flags
# like RATE_LIMIT_DISABLED unset. Absolute paths make loading
# launch-location independent.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (_BACKEND_DIR / ".env", _REPO_ROOT / ".env")

try:
    from dotenv import load_dotenv

    # Later files do not override already-set vars (load_dotenv default
    # override=False), so real environment > backend/.env > root/.env.
    for _env_path in _ENV_FILES:
        if _env_path.is_file():
            load_dotenv(_env_path)
except ImportError:
    pass


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults to False so that *forgetting* to configure it fails safe.
    #
    # DEBUG is not cosmetic: main.py treats it as "this is a dev box" and skips
    # the OAuth issuer downgrade check (`_oauth_is_dev`), which is what keeps an
    # http:// issuer from being stamped into token iss/aud and the discovery
    # document, and it turns on uvicorn --reload.
    #
    # The old default was True, and deploy/docker-stack.prod.yml sets
    # JVSPATIAL_DEBUG -- a *different* variable that this Settings class never
    # reads -- so DEBUG was unset and defaulted True on the production Swarm
    # stack. Production was running with the issuer check disabled and reload
    # enabled. Both the default and the stack files are fixed; the default is
    # the part that keeps the next deployment from repeating it.
    #
    # Local development sets DEBUG=true explicitly (see .env.example).
    DEBUG: bool = False
    WORKERS: int = 1

    # Ceiling on simultaneous chat turns for ONE user. The client caps itself
    # at 5 concurrent streams; nothing on the server did, so a scripted or
    # misbehaving client could open turns without bound — each one an LLM
    # stream holding a connection. Matches the client so an honest client is
    # never refused. Counted per worker; see the WORKERS / WEB_CONCURRENCY
    # warning in main.py.
    MAX_CONCURRENT_TURNS_PER_USER: int = 5

    # JWT signing key consumed by integral app code (ws auth, service
    # auth, tests). Reads only the jvspatial canonical env var —
    # ``JVSPATIAL_JWT_SECRET_KEY`` is the single source of truth so
    # integral's auth stays in lockstep with jvspatial's auth middleware.
    # Must be >= 32 chars (enforced in main.py when not TESTING).
    SECRET_KEY: str = Field(
        default="integral-local-dev-only-change-me-openssl-rand-hex-32__________",
        validation_alias="JVSPATIAL_JWT_SECRET_KEY",
    )
    ALGORITHM: str = Field(
        default="HS256",
        validation_alias="JVSPATIAL_JWT_ALGORITHM",
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        validation_alias="JVSPATIAL_JWT_EXPIRE_MINUTES",
    )
    # Refresh tokens are the upper bound on how long an active user can
    # stay signed in without re-entering credentials. The frontend
    # extends the session by exchanging this token for a fresh access
    # token while the user is active; idle clients let the access token
    # expire and are logged out on next request.
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=7,
        validation_alias="JVSPATIAL_REFRESH_EXPIRE_DAYS",
    )
    # Rotate refresh tokens on every /auth/refresh call. Each refresh
    # issues a new refresh token and revokes the previous one, so a
    # stolen refresh token is invalidated as soon as the legitimate
    # client refreshes again.
    REFRESH_TOKEN_ROTATION: bool = Field(
        default=True,
        validation_alias="JVSPATIAL_REFRESH_TOKEN_ROTATION",
    )
    REGISTRATION_OPEN: bool = True

    ADMIN_EMAIL: Optional[str] = None
    ADMIN_PASSWORD: Optional[str] = None
    ADMIN_NAME: str = "Admin"

    INTEGRAL_SERVICE_KEY: Optional[str] = None

    # Shared with the SPA (``VITE_WEB_ASSET_VERSION``). Deploy pipelines must
    # stamp the same value on API + frontend so stale browser tabs can detect
    # a new release via ``GET /api/meta/build`` and auto-reload once.
    WEB_ASSET_VERSION: str = Field(
        default="0.1.0",
        validation_alias="INTEGRAL_WEB_ASSET_VERSION",
    )

    # D-02 (Plan 01-02): User auto-create on first service-auth call is opt-in.
    # Default OFF for security. When ON, every successful auto-create logs at
    # INFO with caller-key fingerprint and the resolved AuthUser id.
    # NOTE: the middleware itself reads this env var at request-time via
    # os.getenv() so test monkeypatching applies immediately; this Settings
    # field is the documented declarative source-of-truth and serves as a
    # default for any non-middleware reader.
    INTEGRAL_SERVICE_AUTO_CREATE_USERS: bool = False

    # Comma-separated auth user ids the service key may impersonate via
    # X-Integral-User-Id. Empty = unrestricted (legacy). Non-empty = allowlist.
    # Production deployments SHOULD set this (or leave empty only for embed
    # paths that mint a fixed resident principal). See Full Sweep S1.
    INTEGRAL_SERVICE_ALLOWED_USER_IDS: str = ""

    # In-app chat → jvagent (server-side connector; browser never calls jvagent directly)
    # Two delivery modes:
    # - EMBED: jvagent runs in-process via jvagent.embed.bootstrap on startup
    #   when agent/app.yaml exists. The active agent is picked by the user via
    #   the /agent surface and persisted as the jvspatial Agent node id on the
    #   ChatThread row.
    # - HTTP: out-of-process jvagent server (single-agent only) reached via
    # JVAGENT_BASE_URL + INTEGRAL_JVAGENT_AGENT_ID.
    JVAGENT_BASE_URL: str = ""
    INTEGRAL_JVAGENT_AGENT_ID: Optional[str] = None

    # Wall-clock ceiling for one agent turn over the HTTP connector, which is a
    # single blocking POST /interact for the whole turn. A reasoning model that
    # plans, reads a schema, queries and writes runs for minutes, not seconds:
    # measured turns on the resident harness reach ~8 minutes. The old 120s
    # client timeout cut those off and reported "Could not reach the agent
    # service" — a transport error for what was a healthy, still-running turn.
    # The orchestrator bounds the turn itself (activation_budget,
    # max_duration_seconds); this is only the backstop against a hung socket.
    INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS: float = 900.0

    # jvagent embed bootstrap update mode (run | merge | source).
    # - ``source`` (default): YAML is the source of truth. Every action node is
    #   rebuilt from agent.yaml on each restart, so context.* overrides
    #   (model, skills, prompts, response_mode, routing flags) always
    #   propagate cleanly. Right default for development.
    # - ``merge``: update action metadata + module_path in place but preserve
    #   runtime property values. Right for production where API-driven drift
    #   must survive restarts.
    # - ``run``: skip existing actions entirely; only register new ones.
    # NOTE: main.py reads this at startup via os.getenv() (see
    # ``_jvagent_update_mode``) so test/env overrides apply immediately; this
    # field is the documented declarative source-of-truth and default.
    JVAGENT_UPDATE_MODE: str = "source"

    # jvagent's embed ``Server.get_app()`` mounts jvagent's OWN HTTP surface on
    # this app (``/api/agents/{id}/memory/...``, ``/api/actions/{id}``,
    # ``/api/logs``, ``/api/graph``, ...) unless
    # ``JVAGENT_EMBED_ENDPOINTS_DISABLED`` is truthy in the environment. None
    # of it is used by Integral — the resident is reached through the chat
    # surface and MCP only — and the ``auth=False`` interact routes live one
    # import away in the same registration path. Default OFF: main.py sets
    # the kill-switch env var before the Server is built unless this opts in.
    JVAGENT_EMBED_ENDPOINTS_ENABLED: bool = False

    # ===== Agent model credentials (BYOK) =====
    # hybrid: use workspace owner's BYOK when configured, else platform env keys.
    # byo_strict: require owner BYOK — no platform-key fallback.
    # platform_only: ignore stored BYOK; central keys only.
    INTEGRAL_AGENT_KEY_MODE: str = "hybrid"
    INTEGRAL_CREDENTIAL_ENC_KEY: Optional[str] = None
    INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS: Optional[str] = None

    # ===== Voice input (speech-to-text) =====
    # The provider key comes from the workspace owner's model credential
    # (``speech`` slot), resolved under INTEGRAL_AGENT_KEY_MODE like chat.
    # The platform OPENAI_API_KEY — the same variable the embedded agent reads
    # for chat — backs voice input only when SPEECH_ALLOW_PLATFORM_KEY is on:
    # dictation on the platform key bills the deployment for every workspace.
    OPENAI_API_KEY: Optional[str] = None
    SPEECH_ALLOW_PLATFORM_KEY: bool = False
    SPEECH_STREAM_MODEL_DEFAULT: str = "gpt-live-transcribe"
    SPEECH_BATCH_MODEL_DEFAULT: str = "gpt-transcribe"
    # Browser session credentials are only checked when the session opens, so
    # a short TTL bounds how long a leaked one is useful; the client-side cap
    # bounds how long one session runs.
    SPEECH_SESSION_TTL_SECONDS: int = 60
    SPEECH_MAX_SESSION_SECONDS: int = 300
    # "<count>/<seconds>" per user, in-process (single API worker, ADR-005).
    SPEECH_SESSION_RATE_LIMIT: str = "20/60"
    SPEECH_TRANSCRIBE_RATE_LIMIT: str = "10/300"
    SPEECH_TRANSCRIBE_MAX_BYTES: int = 25 * 1024 * 1024
    SPEECH_TRANSCRIBE_TIMEOUT_SECONDS: float = 120.0

    LOG_LEVEL: str = "INFO"
    DB_LOGGING_ENABLED: bool = Field(
        default=True,
        validation_alias="JVSPATIAL_DB_LOGGING_ENABLED",
    )
    DB_LOGGING_LEVELS: str = Field(
        default="ERROR,CRITICAL",
        validation_alias="JVSPATIAL_DB_LOGGING_LEVELS",
    )
    DB_LOGGING_DB_NAME: str = Field(
        default="logs",
        validation_alias="JVSPATIAL_DB_LOGGING_DB_NAME",
    )
    DB_LOGGING_API_ENABLED: bool = Field(
        default=True,
        validation_alias="JVSPATIAL_DB_LOGGING_API_ENABLED",
    )
    LOG_DB_TYPE: str = Field(
        default="",
        validation_alias="JVSPATIAL_LOG_DB_TYPE",
    )
    LOG_DB_PATH: str = Field(
        default="integral_logs",
        validation_alias="JVSPATIAL_LOG_DB_PATH",
    )

    # ===== ChangeEvent storage =====
    # Master kill switch. When False, emit_change_event is a no-op, the audit
    # log + event feed return empty, the WS feed closes 1008, and the TTL
    # reclaim loop is not spawned. ChangeEvent rows are stored as DBLog rows
    # with log_level="CHANGE_EVENT" in the logging database
    # (DB_LOGGING_DB_NAME); see app/services/change_event_logger.py.
    CHANGE_EVENT_ENABLED: bool = True

    # TTL reclaim sweep bounds (app/services/change_event_ttl.py). The sweep
    # pages the reclaimable set instead of hydrating it whole: the first pass
    # after the TTL is crossed / lowered, or after an outage longer than the
    # TTL window, can match an unbounded number of rows.
    #   BATCH_SIZE     — rows hydrated + saved per query.
    #   MAX_PER_CYCLE  — hard cap on rows one wake may reclaim; the remainder
    #                    is picked up on the next interval.
    # Both are also readable as env vars of the same name (live override).
    CHANGE_EVENT_RECLAIM_BATCH_SIZE: int = 500
    CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE: int = 10000

    # ===== Password reset =====
    # Reset tokens are single-use, time-limited (default 60 min).
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60
    # Hard cap on attempts before the token is invalidated.
    PASSWORD_RESET_MAX_ATTEMPTS: int = 5
    # Minimum new-password length (signup also enforces 6).
    PASSWORD_MIN_LENGTH: int = 6

    # ===== Email verification =====
    EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES: int = 15
    EMAIL_VERIFICATION_MAX_ATTEMPTS: int = 5

    # ===== Transactional email =====
    # Provider: "console" | "resend" | "sendgrid"
    EMAIL_PROVIDER: str = "console"
    RESEND_API_KEY: Optional[str] = None
    SENDGRID_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "noreply@gointegral.app"
    EMAIL_FROM_NAME: str = "Integral"
    # Public web origin used to build links inside email bodies.
    APP_BASE_URL: str = "http://localhost:9006"

    # ===== Invitations (Phase 3a) =====
    INVITATION_EXPIRY_DAYS: int = 14

    # ===== OAuth-secured MCP server (M2b Task 1) =====
    # Turns on jvspatial's M1 OAuth Authorization Server + Resource-Server
    # surface (wired in app/main.py's AuthConfig). When the AuthConfig flags
    # are on, jvspatial auto-mounts /api/oauth/{authorize,token,register,revoke},
    # the root /.well-known/{oauth-authorization-server,oauth-protected-resource}
    # + jwks.json discovery documents, and emits an RFC 9728 §5.1
    # WWW-Authenticate challenge on 401s so MCP clients can self-discover the AS.
    #
    # OAUTH_ISSUER_URL is the https origin stamped into token `iss`, the AS/PRM
    # metadata, and the WWW-Authenticate `resource_metadata` pointer. It MUST be
    # the externally-reachable origin in production (a public HTTPS URL); the
    # localhost default is dev-only. OAUTH_SUPPORTED_SCOPES is the advertised
    # scope set (RBAC permission strings) surfaced in the discovery documents.
    OAUTH_ISSUER_URL: str = "http://localhost:4000"
    OAUTH_SUPPORTED_SCOPES: list[str] = Field(
        default_factory=lambda: [
            "integral",
            "integral:read",
            "integral:propose",
            "integral:execute",
        ]
    )

    # Origin of the Integral SPA (the Vite dev server origin by default). When an
    # unauthenticated browser hits jvspatial's GET /api/oauth/authorize, M3a
    # 302-redirects it to FRONTEND_ORIGIN + "/oauth/authorize" (wired in
    # app/main.py's AuthConfig.oauth_authorize_login_redirect), where the SPA
    # drives consent via the bearer-authed /api/oauth/consent endpoints. MUST be
    # the externally-reachable SPA origin in production.
    FRONTEND_ORIGIN: str = "http://localhost:9006"

    # ===== Official MCP Registry browse (ADR-010 enrichment) =====
    MCP_REGISTRY_BASE_URL: str = "https://registry.modelcontextprotocol.io"
    MCP_REGISTRY_HTTP_TIMEOUT_SECONDS: float = 15.0
    # Comma-separated fnmatch patterns. Empty = deny all registry installs
    # (fail closed). Set e.g. ``com.example/*,io.github.org/*`` to allow.
    # ``TESTING=1`` treats empty as allow-all for unit tests.
    MCP_REGISTRY_INSTALL_ALLOWLIST: str = ""
    # Retained for config compatibility; it no longer enables anything. A
    # stdio mount spawns a process on the API host, so the spawn command must
    # come from the in-repo connector catalog (see
    # ``mcp_client._resolve_trusted_stdio_command``) — a registry recipe has no
    # catalog entry to vet against, so ``to_mount_request`` refuses stdio
    # registry installs outright regardless of this flag.
    MCP_REGISTRY_ENABLE_STDIO_INSTALL: bool = False

    # ===== QuickBooks Online connector (Phase 18 QB-01) =====
    # Deployment secrets — single Intuit OAuth app per Integral deployment.
    # NEVER persisted to Connector.auth_state and NEVER logged. Per-company
    # realmId + tokens live on Connector.auth_state (per-connector scope).
    QUICKBOOKS_CLIENT_ID: Optional[str] = None
    QUICKBOOKS_CLIENT_SECRET: Optional[str] = None
    QUICKBOOKS_REDIRECT_URI: str = (
        "http://localhost:9006/connectors/quickbooks/callback"
    )
    # "production" | "sandbox". Sandbox routes to
    # https://sandbox-quickbooks.api.intuit.com instead of the production host.
    QUICKBOOKS_ENVIRONMENT: str = "sandbox"

    # ===== Gmail connector (Phase 19 EML-01) =====
    # Deployment secrets — single Google OAuth app per Integral deployment.
    # NEVER persisted to Connector.auth_state and NEVER logged.
    GMAIL_OAUTH_CLIENT_ID: Optional[str] = None
    GMAIL_OAUTH_CLIENT_SECRET: Optional[str] = None
    GMAIL_OAUTH_REDIRECT_URI: str = (
        "http://localhost:9006/connectors/gmail/oauth/callback"
    )
    # Optional aliases for Google MCP connectors (Drive). Fall back to the
    # Gmail client when unset. Drive consent uses MCP_OAUTH_REDIRECT_URI,
    # not GMAIL_OAUTH_REDIRECT_URI — register both URIs on the Google app.
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = None
    GOOGLE_OAUTH_CLIENT_SECRET: Optional[str] = None
    MCP_OAUTH_REDIRECT_URI: Optional[str] = None

    # ===== Production hardening =====
    # Set ENABLE_HSTS=1 only when the server is exclusively reached over
    # HTTPS at a stable hostname. Locking HSTS into a browser for a dev
    # host effectively breaks plain http:// access for that origin.
    ENABLE_HSTS: bool = False

    # Sentry — opt-in. When DSN is empty/missing the sentry-sdk is never
    # imported and the integration stays inert.
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_RELEASE: Optional[str] = None
    SENTRY_SEND_PII: bool = False

    # Per-endpoint auth rate limits, "<count>/<seconds>" form. Empty
    # values fall back to safer defaults inside the middleware.
    RATE_LIMIT_LOGIN: str = ""
    RATE_LIMIT_REGISTER: str = ""
    RATE_LIMIT_FORGOT: str = ""
    RATE_LIMIT_RESET: str = ""
    RATE_LIMIT_DISABLED: bool = False
    # Unauthenticated public-share surface (app/api/tracks_public_share.py).
    # Writes are the expensive ones -- an anonymous entry create runs the
    # re-embed and the entry-save hooks -- while reads need a bound so the
    # share token itself cannot be brute-forced. Form: "<count>/<seconds>".
    RATE_LIMIT_PUBLIC_SHARE_WRITE: str = ""
    RATE_LIMIT_PUBLIC_SHARE_READ: str = ""
    # Agent / MCP / chat surfaces (Full Sweep S2). Defaults applied in middleware
    # when empty. Multi-worker deployments multiply these buckets — document in
    # ops runbooks; shared Redis limiter is a follow-up.
    RATE_LIMIT_MCP: str = ""
    RATE_LIMIT_AGENTIVE: str = ""
    RATE_LIMIT_CHAT: str = ""
    # Comma-separated IPs / CIDRs whose X-Forwarded-For header may be trusted.
    # Empty (the default) means "trust private and loopback peers", which
    # matches the compose/Swarm topology where the reverse proxy shares a
    # private network with the API and no external client connects directly.
    # Set explicitly to lock it down further. See middleware/rate_limit.py.
    RATE_LIMIT_TRUSTED_PROXIES: str = ""

    # ===== Attachments / file handling (Phase 1 + 1.5) =====
    # Per-file ceiling for uploads. Streams to a temp file, so memory cost
    # stays bounded regardless of value. 500 MB default covers the vast
    # majority of office / PDF / image / audio workflows; raise via env
    # only after confirming the deployment can absorb the latency cost.
    # Files larger than this should land via chunked/resumable upload
    # (Phase 6 — not yet implemented).
    ATTACHMENT_MAX_UPLOAD_BYTES: int = 500 * 1024 * 1024
    # Aggregate cap for a single batch request.
    ATTACHMENT_MAX_BATCH_BYTES: int = 1024 * 1024 * 1024
    # Maximum files per batch request.
    ATTACHMENT_MAX_BATCH_FILES: int = 10
    # When True, attachment uploads that would exceed the organization
    # storage quota are rejected. When False (default), quotas are tracked
    # for observability/UX only.
    STORAGE_QUOTA_ENFORCE: bool = False
    # Cap on full-text content stored on Attachment.extracted_text and
    # pushed to the search index. 10 MB of text ~ 2,000 PDF pages.
    ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES: int = 10 * 1024 * 1024
    # Cap on extracted text returned to the resident agent per
    # integral_get_attachment_text call. The 10 MB storage cap above is far
    # too large for a model context window, so the agent read path truncates
    # to a context-safe slice (~5k tokens) and flags ``truncated``.
    ATTACHMENT_AGENT_TEXT_MAX_CHARS: int = 20000
    # Current metadata extractor schema version. Attachments with
    # ``metadata_extractor_version`` below this value are re-extracted on
    # demand (via /attachments/{id}/reprocess or a future admin sweep).
    ATTACHMENT_METADATA_EXTRACTOR_VERSION: int = 1
    # Antivirus / malware scanner identifier. ``noop`` disables scanning
    # (status flips to ``skipped``). Wire a real engine (e.g. ``clamav``)
    # via the AttachmentScanner registry when ready.
    ATTACHMENT_SCANNER: str = "noop"

    # ===== Avatar pipeline (Phase 9 Plan 09-01, AVT-01) =====
    # Dedicated cap per locked decision A4. NOT shared with
    # ATTACHMENT_MAX_UPLOAD_BYTES — avatar uploads are validated
    # before reaching Pillow to bound CPU exposure (DoS hardening
    # against decompression-bomb PNGs).
    AVATAR_MAX_UPLOAD_BYTES: int = 5_000_000  # 5 MB
    AVATAR_ALLOWED_MIMES: tuple = ("image/png", "image/jpeg", "image/webp")
    AVATAR_SIZES: tuple = (32, 64, 128, 256)

    # ===== Notification routing (Phase 9 Plan 09-03a — NOTIF-02) =====
    # Master switch — when False, only the in_app channel fires (escape
    # hatch for dev/test environments where outbound email / WhatsApp
    # would be noisy). Channel adapters still run, but the router only
    # appends in_app to the channels_to_fire list when this flag is off.
    # WHATSAPP_* settings land in Plan 09-03b alongside the real WhatsApp
    # adapter; the stub in this plan ignores them.
    NOTIFICATION_OUTBOUND_ENABLED: bool = True

    # ===== WhatsApp Cloud API (Phase 9 Plan 09-03b — NOTIF-02 / A3) =====
    # First message uses pre-approved welcome template to open the 24h
    # customer-service window. Subsequent messages within 24h after each
    # user-initiated reply may be freeform.
    #
    # When ``WHATSAPP_CLOUD_API_TOKEN`` or ``WHATSAPP_PHONE_NUMBER_ID`` is
    # empty, ``services.whatsapp_service.send_whatsapp_message`` returns
    # ``(False, None)`` without making any HTTP call — the missing-creds
    # path is a console-provider fallback for dev/CI environments.
    WHATSAPP_CLOUD_API_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_API_BASE: str = "https://graph.facebook.com/v18.0"
    WHATSAPP_WELCOME_TEMPLATE_NAME: str = "integral_welcome_v1"
    WHATSAPP_WELCOME_TEMPLATE_LANG: str = "en_US"

    # ===== F0 — Core / App package boundary =====
    # Comma-separated absolute or repo-relative roots walked for profile.yaml
    # packages. Empty = ``backend/app/profiles/`` (Core seeds) plus
    # ``packages/apps/`` when that directory exists (commercial monorepo).
    # Core-only boots still filter to ``core_package`` via INTEGRAL_CORE_ONLY.
    INTEGRAL_PACKAGE_PATHS: str = ""
    # When True, library sync + catalog load only ``core_package`` artifacts
    # (agent-scratch). Domain Apps (e.g. personal-context) must be installed
    # from an explicit package path or after disabling this flag.
    INTEGRAL_CORE_ONLY: bool = False

    # ===== Chunked / resumable uploads (Plan 03 — Phase 6) =====
    # When False (the default) the chunked upload endpoints reject with
    # 503 so the client falls back to single-request multipart. Flip on
    # once you've confirmed the storage backend handles the staging
    # prefix cleanly (S3 multipart-of-our-own makes the most sense; the
    # local provider just writes parts into the temp prefix and merges).
    CHUNKED_UPLOAD_ENABLED: bool = False
    # 8 MiB chunks balance "few requests per upload" with "small enough
    # that one failed chunk is cheap to retry". Clients may negotiate
    # smaller chunks; larger requests are rejected by the same per-file
    # limit that gates the non-chunked path.
    CHUNKED_UPLOAD_CHUNK_SIZE_BYTES: int = 8 * 1024 * 1024
    # Upload sessions that don't reach ``complete`` within this window
    # are reaped by the scheduled sweep (future ops work) and their
    # staged chunks deleted. 24 h covers "user closed the laptop, came
    # back the next morning".
    CHUNKED_UPLOAD_SESSION_TTL_HOURS: int = 24
    # Maximum total bytes a single chunked upload session may declare.
    # Defaults to 10x the single-shot per-file ceiling so the resumable
    # path is materially better than just retrying the simple upload.
    CHUNKED_UPLOAD_MAX_TOTAL_BYTES: int = 5 * 1024 * 1024 * 1024

    @field_validator("ADMIN_EMAIL", mode="before")
    @classmethod
    def empty_admin_email_to_none(cls, v: object) -> object:
        """Treat empty ADMIN_EMAIL env var as unset."""
        if v == "":
            return None
        return v

    @field_validator("ADMIN_PASSWORD", mode="before")
    @classmethod
    def empty_admin_password_to_none(cls, v: object) -> object:
        """Treat empty ADMIN_PASSWORD env var as unset."""
        if v == "":
            return None
        return v

    @model_validator(mode="after")
    def default_log_db_type_from_prime(self) -> Settings:
        """Default LOG_DB_TYPE to mirror the prime DB when unset.

        Logging store uses the same backend family as ``JVSPATIAL_DB_TYPE``
        (postgres, sqlite, mongodb, dynamodb). Set ``JVSPATIAL_LOG_DB_TYPE``
        explicitly to override — e.g. ``json`` for zero-infra local dev.
        """
        if not (self.LOG_DB_TYPE or "").strip():
            prime = os.environ.get("JVSPATIAL_DB_TYPE", "postgres")
            object.__setattr__(self, "LOG_DB_TYPE", prime)
        return self


settings = Settings()
