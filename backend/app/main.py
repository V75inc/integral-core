"""Integral backend — FastAPI application entry point with jvspatial integration."""

import asyncio
import importlib
import logging as std_logging
import os
import re
import sys
from typing import Any, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from app.config import settings


def _running_under_pytest() -> bool:
    """True when this process is a pytest run.

    ``PYTEST_CURRENT_TEST`` is set only while an individual test EXECUTES, so it
    is absent at import time — and this module is imported during collection
    (``from app.main import app``). Checking it alone made the guard below fire
    mid-collection and ``sys.exit(1)`` out of an xdist worker wherever DEBUG is
    off and TESTING is set, i.e. every CI run. ``pytest`` in ``sys.modules`` is
    the import-time-safe signal; nothing imports pytest in a served process.
    """
    return bool(os.getenv("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


# Refuse TESTING/test_mode outside pytest when DEBUG is off — prevents accidental
# production boots with auth bypass middleware enabled.
if (
    (os.getenv("TESTING") or os.getenv("test_mode"))
    and not _running_under_pytest()
    and not settings.DEBUG
):
    print(
        "FATAL: TESTING or test_mode is set without PYTEST_CURRENT_TEST while "
        "DEBUG=False. Refusing to boot with test auth posture in production.",
        file=sys.stderr,
    )
    sys.exit(1)

# Full Sweep S5: even with DEBUG=true, warn loudly — shared staging boxes
# with TESTING=1 still enable TestAuthBypassMiddleware.
if (
    (os.getenv("TESTING") or os.getenv("test_mode"))
    and not _running_under_pytest()
    and settings.DEBUG
):
    print(
        "WARNING: TESTING/test_mode is set with DEBUG=true outside pytest. "
        "TestAuthBypassMiddleware will be active. Do not expose this process.",
        file=sys.stderr,
    )


# Multi-worker degrades two invariants SILENTLY — say it out loud rather than
# let a deployment discover it. Docker images drive uvicorn via
# ``WEB_CONCURRENCY`` (Dockerfile CMD); ``python -m app.main`` uses
# ``settings.WORKERS``. Either knob above 1 is the same hazard.
#
# Both the in-flight turn registry (`chat_turn_registry._in_flight`) and the
# agent-events websocket map (`agent_events._agent_event_connections`) are
# module-level dicts, i.e. per PROCESS. Across workers that means:
#
#   * I-CHAT-PAR-01 (one in-flight turn per thread) holds only within a worker,
#     so two workers can each admit a turn for the same thread;
#   * MAX_CONCURRENT_TURNS_PER_USER is likewise counted per worker, so the real
#     ceiling is the limit times the worker count;
#   * websocket fan-out reaches only clients connected to the emitting worker,
#     so staged-change and thread-status pushes are missed by the rest.
#
# This is a warning, not a refusal: multi-worker is a real scaling need and
# removing it would be worse than running with known limits. The fix is a
# shared registry (Postgres or Redis), which is a deliberate piece of work.
def _effective_worker_count() -> int:
    """Max of WORKERS / WEB_CONCURRENCY (both default 1)."""
    raw = (os.getenv("WEB_CONCURRENCY") or "1").strip()
    try:
        web_concurrency = int(raw)
    except ValueError:
        web_concurrency = 1
    return max(settings.WORKERS, web_concurrency, 1)


if not _running_under_pytest() and _effective_worker_count() > 1:
    _n = _effective_worker_count()
    print(
        f"WARNING: effective workers={_n} "
        f"(WORKERS={settings.WORKERS}, "
        f"WEB_CONCURRENCY={os.getenv('WEB_CONCURRENCY') or '1'}). "
        "The in-flight turn registry and the agent-events websocket map are "
        "per-process, so one-turn-per-thread (I-CHAT-PAR-01) and per-user turn "
        "limits hold only within a worker, and websocket pushes reach only "
        "clients on the emitting worker. Keep WORKERS and WEB_CONCURRENCY at 1, "
        "or move the registry to shared state.",
        file=sys.stderr,
    )

# Enforce SECRET_KEY whenever not under pytest/TESTING (including DEBUG/staging).
_DEFAULT_WEAK_SECRET = "your-secret-key-change-in-production"
# Must stay in sync with ``Settings.SECRET_KEY`` default in ``app/config.py``.
_CONFIG_DEFAULT_SECRET = (
    "integral-local-dev-only-change-me-openssl-rand-hex-32__________"
)
# Placeholders shipped in the env examples. ``replace-with-openssl-rand-hex-32``
# is exactly 32 characters, so the length check below waves it straight through
# — copy an example file, deploy it, and the service boots happily signing JWTs
# with a value published in the repo. Names, not just lengths, have to be
# rejected. (``JVSPATIAL_JWT_SECRET_KEY`` is the alias that feeds
# ``Settings.SECRET_KEY``; see app/config.py.)
_EXAMPLE_PLACEHOLDER_SECRETS = (
    "replace-with-openssl-rand-hex-32",
    "replace-me-openssl-rand-hex-32-______________________",
)
# Any secret containing one of these reads as "nobody filled this in". A real
# `openssl rand -hex 32` output is [0-9a-f]{64} and cannot contain them, so the
# substring test costs nothing and catches placeholders we have not enumerated.
_PLACEHOLDER_MARKERS = (
    "replace-me",
    "replace-with",
    "change-me",
    "changeme",
    "change-in-production",
    "your-secret",
    "placeholder",
)


def _secret_key_is_acceptable(key: str) -> bool:
    k = (key or "").strip()
    if len(k) < 32:
        return False
    if k in (_DEFAULT_WEAK_SECRET, _CONFIG_DEFAULT_SECRET):
        return False
    if k in _EXAMPLE_PLACEHOLDER_SECRETS:
        return False
    lowered = k.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    return True


if not (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")):
    if not _secret_key_is_acceptable(settings.SECRET_KEY):
        print(
            "FATAL: SECRET_KEY must be at least 32 characters and not the default "
            "placeholder (even when DEBUG=True).",
            file=sys.stderr,
        )
        print(
            "Set SECRET_KEY in your environment (e.g. openssl rand -hex 32). "
            "See .env.example.",
            file=sys.stderr,
        )
        sys.exit(1)


# Refuse an insecure OAUTH_ISSUER_URL in production. The issuer is stamped into
# token ``iss``/``aud``, the AS/PRM discovery metadata, and the RFC 9728
# WWW-Authenticate pointer — an ``http://`` issuer on a public origin is a
# downgrade/interception footgun. We allow ``http://`` only for the RFC 8252
# loopback hosts (localhost / 127.0.0.1 / [::1]) — consistent with M1's DCR
# loopback redirect policy — and exempt dev (DEBUG/TESTING) entirely so the
# ``http://localhost:4000`` default still boots locally.
_OAUTH_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def _validate_oauth_issuer(issuer: str, *, is_dev: bool) -> bool:
    """Return True when ``issuer`` is acceptable for the current environment.

    Pure predicate (no env reads) so it is unit-testable. Rules:

    * In dev (``is_dev=True``) anything is accepted — local HTTP is fine.
    * ``https://`` is always accepted.
    * ``http://`` is accepted ONLY for a loopback host (localhost / 127.0.0.1 /
      [::1]); any other ``http://`` origin is rejected in non-dev.
    """
    value = (issuer or "").strip()
    if is_dev:
        return True
    if value.startswith("https://"):
        return True
    if value.startswith("http://"):
        host = value[len("http://") :].split("/", 1)[0]
        host_only = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return host_only in _OAUTH_LOOPBACK_HOSTS
    # No scheme / unexpected scheme -> reject outside dev.
    return False


# oauth_enabled is hardwired True in the AuthConfig below (M2b). The dev gate
# matches the SECRET_KEY guard's prod-detection (not pytest/TESTING) and, per
# the issuer footgun being DEBUG-irrelevant, also treats DEBUG as dev.
_oauth_is_dev = bool(
    os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING") or settings.DEBUG
)
if not _oauth_is_dev:
    if not _validate_oauth_issuer(settings.OAUTH_ISSUER_URL, is_dev=False):
        print(
            "FATAL: OAUTH_ISSUER_URL must be the public https:// origin in "
            "production. The OAuth issuer is stamped into token iss/aud, the "
            "authorization-server / protected-resource discovery metadata, and "
            "the WWW-Authenticate challenge; an http:// issuer on a non-loopback "
            f"origin is a downgrade/interception risk. Got: {settings.OAUTH_ISSUER_URL!r}",
            file=sys.stderr,
        )
        print(
            "Set OAUTH_ISSUER_URL to your externally-reachable https origin "
            "(e.g. https://api.example.com). See .env.example.",
            file=sys.stderr,
        )
        sys.exit(1)

# Refuse an insecure FRONTEND_ORIGIN in production. FRONTEND_ORIGIN is the SPA
# origin jvspatial 302-redirects an unauthenticated ``GET /api/oauth/authorize``
# browser to (the SPA then drives consent). An empty value, or an ``http://``
# origin on a non-loopback host, means the OAuth login redirect points at a
# plaintext / unset target — a downgrade/interception footgun on the consent
# leg. We reuse the issuer predicate's exact policy (https always OK, http only
# for RFC 8252 loopback hosts, anything in dev), so the local
# ``http://localhost:9006`` default still boots while a public ``http://`` SPA
# origin is refused outside dev.
if not _oauth_is_dev:
    if not _validate_oauth_issuer(settings.FRONTEND_ORIGIN, is_dev=False):
        print(
            "FATAL: FRONTEND_ORIGIN must be the public https:// SPA origin in "
            "production. It is the OAuth login-redirect target for the consent "
            "flow; an empty value or an http:// origin on a non-loopback host is "
            f"a downgrade/interception risk. Got: {settings.FRONTEND_ORIGIN!r}",
            file=sys.stderr,
        )
        print(
            "Set FRONTEND_ORIGIN to your externally-reachable https SPA origin "
            "(e.g. https://app.example.com). See .env.example.",
            file=sys.stderr,
        )
        sys.exit(1)

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jvspatial.api import Server as _BaseServer
from jvspatial.api.config_groups import AuthConfig, CORSConfig, DatabaseConfig


def _jvagent_update_mode() -> Optional[str]:
    """Resolve the jvagent embed bootstrap ``update_mode`` from env.

    Reads ``JVAGENT_UPDATE_MODE`` (default ``source``). Accepts ``run``,
    ``merge``, ``source`` case-insensitively; anything else falls back to
    ``source`` with a warning. ``run`` maps to ``None`` because that is the
    bootstrap "skip existing actions" sentinel. Read here via os.getenv so a
    ``.env`` value or test monkeypatch applies at startup; the declarative
    default lives on ``Settings.JVAGENT_UPDATE_MODE``.
    """
    raw = os.getenv("JVAGENT_UPDATE_MODE", "source").strip().lower()
    if raw not in ("run", "merge", "source"):
        std_logging.getLogger("app.agentive").warning(
            "Invalid JVAGENT_UPDATE_MODE %r; falling back to 'source'", raw
        )
        raw = "source"
    return None if raw == "run" else raw


# Resident-action class id-prefixes retired by forward-only renames. jvspatial
# encodes the class in a node id (``n.<Class>.<uuid>``), so renaming the
# resident action class strands the previously-persisted action node under the
# OLD prefix. The dead class can no longer be resolved by the ORM, so jvagent's
# ``register_action`` cannot find that node to reuse/replace — it then collides
# on the ``(agent_id, label)`` unique index
# (``node_context_agent_id_context_label_uniq`` → "duplicate key value") and
# fails to register, breaking agentive startup. Add a retired prefix here
# whenever the resident action class is renamed again.
_DEAD_RESIDENT_ACTION_ID_PREFIXES = ("n.IntegralEmbeddedAction.",)
# The resident action's stable ``context.label`` (unchanged across the class
# rename) — scopes the orphan sweep to just the resident-action nodes.
_RESIDENT_ACTION_LABEL = "embedded_integral_action"


async def _purge_dead_resident_action_orphans() -> None:
    """Drop resident-action nodes orphaned by a forward-only class rename.

    Runs once before the jvagent embed bootstrap. A fresh or already-clean DB
    has no orphans (no-op). The action node is rebuilt from ``agent.yaml`` on
    every ``source``-mode bootstrap, so removing a stale-class orphan (and its
    edges) is non-destructive. Best-effort: any failure here is logged and
    swallowed so it can never block startup. Backend-agnostic — uses the
    jvspatial DB facade (``find``/``delete`` on the ``node``/``edge``
    collections), no raw SQL.
    """
    logger = std_logging.getLogger("app.agentive")
    try:
        from jvspatial.db import get_database_manager

        db = get_database_manager().get_prime_database()
        find = getattr(db, "find", None)
        delete = getattr(db, "delete", None)
        if find is None or delete is None:
            return

        # Scope by the stable label, then keep only ids under a retired
        # (dead-class) prefix — this can never match the live current-class
        # node, so it cannot delete the in-use action.
        rows = await find("node", {"context.label": _RESIDENT_ACTION_LABEL})
        for row in rows or []:
            nid = (row or {}).get("id") or ""
            if not any(nid.startswith(p) for p in _DEAD_RESIDENT_ACTION_ID_PREFIXES):
                continue
            # Remove edges referencing the orphan (source or target) first so no
            # dangling edge survives. Two equality queries (not ``$or``) for the
            # widest backend portability; dedup by edge id.
            edge_ids: set[str] = set()
            for side in ("source", "target"):
                try:
                    side_edges = await find("edge", {side: nid})
                except Exception:
                    side_edges = []
                for edge in side_edges or []:
                    eid = (edge or {}).get("id")
                    if eid:
                        edge_ids.add(eid)
            for eid in edge_ids:
                try:
                    await delete("edge", eid)
                except Exception:
                    pass
            try:
                await delete("node", nid)
                logger.warning(
                    "purged orphaned resident-action node left by a class rename: "
                    "%s (rebuilt from agent.yaml on bootstrap)",
                    nid,
                )
            except Exception:
                logger.debug("failed to delete orphan node %s", nid, exc_info=True)
    except Exception:
        logger.debug("dead-resident-action orphan purge skipped", exc_info=True)


# Drop-in Server class: use jvagent.embed.Server when available so its
# overridden get_app() auto-registers jvagent's HTTP routes on this server
# before FastAPI snapshots the endpoint router.
try:
    from jvagent.embed import Server  # type: ignore[assignment]
except ImportError as _exc:  # noqa: F841 — message includes exc
    import logging as _std_logging

    _std_logging.getLogger("app.agentive").warning(
        "jvagent.embed.Server import failed (%s); "
        "falling back to plain jvspatial.api.Server. jvagent HTTP routes "
        "will not be available.",
        _exc,
    )
    Server = _BaseServer  # type: ignore[assignment, misc]

from jvspatial.env import env, parse_bool, parse_csv
from jvspatial.logging import configure_standard_logging, initialize_logging_database

# Extra auth-exempt paths must be explicitly allowlisted — no wildcards.
_AUTH_EXEMPT_EXTRA_ALLOWLIST = frozenset(
    {
        "/api/health/ready",
        "/api/meta/build",
        "/api/public-share/",
        "/api/public-share/track/",
        "/api/portfolio/shared/",
        "/api/invitations/",
    }
)


def _auth_exempt_extra_allowed(path: str) -> bool:
    normalized = (path or "").strip()
    if not normalized:
        return False
    if normalized in _AUTH_EXEMPT_EXTRA_ALLOWLIST:
        return True
    return any(
        prefix.endswith("/") and normalized.startswith(prefix)
        for prefix in _AUTH_EXEMPT_EXTRA_ALLOWLIST
    )


def _validate_auth_exempt_paths_extra(extra_paths: list[str]) -> list[str]:
    validated: list[str] = []
    log = std_logging.getLogger("app.main")
    is_dev = bool(
        settings.DEBUG or os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")
    )
    for raw in extra_paths:
        path = (raw or "").strip()
        if not path:
            continue
        if _auth_exempt_extra_allowed(path):
            validated.append(path)
            continue
        msg = f"INTEGRAL_AUTH_EXEMPT_PATHS_EXTRA contains disallowed path: {path!r}"
        if is_dev:
            log.warning("%s (ignored in dev/test)", msg)
            continue
        print(f"FATAL: {msg}", file=sys.stderr)
        sys.exit(1)
    return validated


_AUTH_EXEMPT_PATHS_EXTRA = _validate_auth_exempt_paths_extra(
    env("INTEGRAL_AUTH_EXEMPT_PATHS_EXTRA", default=[], parse=parse_csv)
)

from app.models.nodes import (
    App,  # Phase 10 / Plan 10-01 — user-facing primitive (formerly ``Space``).
)
from app.models.nodes import (
    Approval,  # Phase 7 Plan 07-04 — deferred-approval agent write (core)
)
from app.models.nodes import (
    Apps,  # Phase 10 / Plan 10-01 — per-workspace registry (formerly ``Spaces``).
)
from app.models.nodes import (
    Conflict,  # Phase 5 Plan 05-03 — connector-sync conflict record (core)
)
from app.models.nodes import (
    IntegralApp,  # Phase 10 / Plan 10-01 — renamed from ``App`` (singleton).
)
from app.models.nodes import Policy  # Phase 3 D-02 — core authorization infrastructure
from app.models.nodes import (
    Skill,  # Phase 10 / Plan 10-04 — App-bundled skill (APP-SKILLS-01)
)
from app.models.nodes import (
    Attachment,
    ChatMessage,
    ChatThread,
    ChatThreads,
    Comment,
    ContentProfile,
    ContentProfiles,
    Dashboard,
    Dashboards,
    Entry,
    EntryType,
    Invitation,
    Invitations,
    Notification,
    Tag,
    Track,
    Tracks,
    User,
    Users,
    View,
    Views,
    Workspace,
    Workspaces,
)


async def _warm_embedding_model() -> None:
    """RET-02 — eagerly warm the ONNX embedding singleton at boot.

    Gated on ``EMBEDDING_MODEL_EAGER_LOAD`` (default ``"1"``) AND on the
    deployment actually having a vector store (Wave 2 of the
    SaaS-deployment plan): when ``semantic_retrieval_available()`` is
    False the ``null`` driver is active, so loading the model would burn
    ~200 MB of memory + a slow cold-start for code paths that will never
    use it. Tests flip the env var OFF for faster boot. Failure is
    logged-and-swallowed — a missing wheel must not prevent the API from
    booting; the lazy load on first ``embed_entry_text`` call will
    surface the same error there.
    """

    if os.environ.get("EMBEDDING_MODEL_EAGER_LOAD", "1") != "1":
        return
    try:
        from app.services.retrieval import semantic_retrieval_available

        if not semantic_retrieval_available():
            std_logging.getLogger("app.services.retrieval").info(
                "embedding model warm-load skipped: semantic retrieval "
                "unavailable (null driver active)"
            )
            return
    except Exception as exc:  # noqa: BLE001
        std_logging.getLogger("app.services.retrieval").warning(
            "embedding model warm-load gate check failed; proceeding "
            "with eager load: %s",
            exc,
        )

    try:
        from app.services.retrieval.embedding_model import load_model

        await asyncio.to_thread(load_model)
        std_logging.getLogger("app.services.retrieval").info(
            "embedding model warm-loaded at startup"
        )
    except Exception as exc:  # noqa: BLE001
        std_logging.getLogger("app.services.retrieval").warning(
            "embedding model warm-load failed (lazy load will retry): %s",
            exc,
        )


async def _ensure_atlas_search_index() -> None:
    """Provision the Atlas Vector Search index when the ``atlas`` driver is active.

    Idempotent — the driver lists existing search indexes and creates
    the canonical definition only if absent. Atlas builds indexes
    asynchronously; this call returns immediately once the create
    request is accepted. Logged-and-swallowed inside the driver so a
    self-hosted Mongo (no Atlas Vector Search) or restricted privileges
    do not block boot — operators provision manually per DEPLOY.md in
    that case.
    """
    try:
        from app.services.retrieval import _resolve_driver_name, get_embedding_store

        if _resolve_driver_name() != "atlas":
            return
        store = get_embedding_store()
        ensure = getattr(store, "ensure_search_index", None)
        if ensure is None:
            return
        await ensure()
    except Exception as exc:  # noqa: BLE001
        std_logging.getLogger("app.services.retrieval").warning(
            "atlas search-index provisioning failed (manual setup may be required): %s",
            exc,
        )


# Module-level handle for every background task spawned at startup
# (ttl_reclaim_loop, sync_loop, approval_ttl_loop, app_install_reaper,
# …). ``_shutdown`` cancels all of these BEFORE closing the DB pool so
# a final tick can't fire mid-pool-shutdown and surface as
# "pool is closing / pool is closed" warnings.
_background_tasks: list[asyncio.Task] = []


def _auto_create_indexes_enabled() -> bool:
    """``JVSPATIAL_AUTO_CREATE_INDEXES`` — default on; ``0``/``false``/``no`` off."""
    return os.environ.get("JVSPATIAL_AUTO_CREATE_INDEXES", "true").lower() not in (
        "0",
        "false",
        "no",
    )


# Modules whose Node / Edge subclasses get ``ensure_indexes`` at boot. The
# agentive modules are listed explicitly: ``RoutineTask.get_indexes()`` backs
# the scheduler's every-tick due-task query and was never materialized while
# the loop only walked ``app.models``.
_INDEXED_MODEL_MODULES = (
    "app.models.nodes",
    "app.models.edges",
    "app.agentive.nodes",
    "app.agentive.edges",
)


def _collect_index_classes() -> list[type]:
    """Every concrete Node / Edge subclass exported by ``_INDEXED_MODEL_MODULES``."""
    from jvspatial.core import Edge as _Edge
    from jvspatial.core import Node as _Node

    classes: list[type] = []
    seen: set = set()
    for mod_name in _INDEXED_MODEL_MODULES:
        mod = importlib.import_module(mod_name)
        for _name in dir(mod):
            cls = getattr(mod, _name)
            if (
                isinstance(cls, type)
                and issubclass(cls, (_Node, _Edge))
                and cls not in (_Node, _Edge)
                and cls not in seen
            ):
                seen.add(cls)
                classes.append(cls)
    return classes


async def _ensure_model_indexes() -> None:
    """Run ``ensure_indexes`` for every indexed model class plus credentials."""
    log = std_logging.getLogger(__name__)
    work_index_fatal: Optional[Exception] = None
    try:
        from jvspatial.core.context import get_default_context

        ctx_for_indexes = get_default_context()
        index_classes = _collect_index_classes()
        for cls in index_classes:
            try:
                await ctx_for_indexes.ensure_indexes(cls)
            except Exception as ix_err:  # noqa: BLE001
                log.warning("ensure_indexes failed for %s: %s", cls.__name__, ix_err)
        # ``UserModelCredential`` is an Object (I-GRAPH-02), not a Node, so it
        # is not picked up by the Node/Edge walk above.
        try:
            from app.models.credentials import UserModelCredential
            from app.services.model_credentials import (
                dedupe_user_model_credentials,
            )

            removed = await dedupe_user_model_credentials()
            if removed:
                log.info(
                    "dedupe_user_model_credentials removed %d duplicate row(s)",
                    removed,
                )
            await ctx_for_indexes.ensure_indexes(UserModelCredential)
        except Exception as cred_ix_err:  # noqa: BLE001
            log.warning(
                "ensure_indexes failed for UserModelCredential: %s", cred_ix_err
            )
        try:
            from app.models.install_attempt import InstallAttempt

            await ctx_for_indexes.ensure_indexes(InstallAttempt)
        except Exception as ia_err:  # noqa: BLE001
            log.warning("ensure_indexes failed for InstallAttempt: %s", ia_err)
        try:
            from app.models.operation_idempotency import OperationIdempotencyRecord

            await ctx_for_indexes.ensure_indexes(OperationIdempotencyRecord)
        except Exception as op_idem_err:  # noqa: BLE001
            log.warning(
                "ensure_indexes failed for OperationIdempotencyRecord: %s",
                op_idem_err,
            )
        try:
            from app.agentive.services.execution_runs import AgentRun, RunStep

            await ctx_for_indexes.ensure_indexes(AgentRun)
            await ctx_for_indexes.ensure_indexes(RunStep)
        except Exception as run_ix_err:  # noqa: BLE001
            log.warning("ensure_indexes failed for AgentRun/RunStep: %s", run_ix_err)
        # F3: Entitlement is an Object (I-GRAPH-02), same pattern as credentials.
        try:
            from app.models.entitlement import Entitlement

            await ctx_for_indexes.ensure_indexes(Entitlement)
        except Exception as ent_ix_err:  # noqa: BLE001
            log.warning("ensure_indexes failed for Entitlement: %s", ent_ix_err)
        try:
            from app.models.query_result_set import QueryResultSet

            await ctx_for_indexes.ensure_indexes(QueryResultSet)
        except Exception as result_set_ix_err:  # noqa: BLE001
            log.warning(
                "ensure_indexes failed for QueryResultSet: %s",
                result_set_ix_err,
            )
        # Durable work kernel Objects (I-GRAPH-02). Production fails closed —
        # workers cannot safely claim without these indexes.
        try:
            from app.agentive.work_models import (
                ChangeEventTriggerCheckpoint,
                EventTriggerDeclaration,
                WorkApproval,
                WorkItem,
                WorkOutboxEntry,
            )

            for work_cls in (
                WorkItem,
                WorkOutboxEntry,
                WorkApproval,
                EventTriggerDeclaration,
                ChangeEventTriggerCheckpoint,
            ):
                await ctx_for_indexes.ensure_indexes(work_cls)
        except Exception as work_ix_err:  # noqa: BLE001
            _dev = bool(
                settings.DEBUG
                or os.getenv("PYTEST_CURRENT_TEST")
                or os.getenv("TESTING")
            )
            if not _dev:
                work_index_fatal = work_ix_err
            else:
                log.warning(
                    "ensure_indexes failed for work kernel models: %s",
                    work_ix_err,
                )
        log.info("ensure_indexes ran for %d Node/Edge classes", len(index_classes))
    except Exception as outer_err:  # noqa: BLE001
        log.warning("ensure_indexes startup loop failed: %s", outer_err)
    if work_index_fatal is not None:
        raise work_index_fatal


async def _startup() -> None:
    """Configure logging and optional DB logging at server startup."""
    # Content-profile code-plugin discovery runs unconditionally (including
    # under TESTING) so that test runs see the same registry surface a real
    # boot does. Built-in primitives are registered at module import time;
    # this picks up directory + entry-point plugins.
    from app.services.content_profile_plugins import discover_and_register_plugins

    discover_and_register_plugins()

    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING"):
        return

    # RET-02 eager warm — runs after the TESTING gate so test boots stay
    # fast; tests that want to exercise the warmup invoke
    # ``_warm_embedding_model`` directly.
    await _warm_embedding_model()

    # Wave 3 — provision Atlas Vector Search index when the atlas driver
    # is active. Idempotent (the driver lists existing indexes and skips
    # if present). Failure is logged-and-swallowed inside ensure_search_index;
    # operators can fall back to manual provisioning per DEPLOY.md.
    await _ensure_atlas_search_index()

    from app.bootstrap_admin import bootstrap_admin_if_needed
    from app.services.app_graph import ensure_integral_app_graph

    await ensure_integral_app_graph()
    await bootstrap_admin_if_needed()

    # Embedded jvagent runtime — share integral's jvspatial DB + auth context.
    from pathlib import Path

    from jvagent import embed as _jvagent_embed

    # main.py is at backend/app/main.py → repo root is parents[2].
    _agent_root = Path(__file__).resolve().parents[2] / "agent"
    if (_agent_root / "app.yaml").exists():
        # ``source`` (the default) rebuilds every action node from
        # agent.yaml on every restart, making the YAML the source of
        # truth — context.* overrides (model, skills, prompts,
        # response_mode, routing flags) all propagate cleanly. The
        # alternative ``merge`` mode only copies action metadata +
        # module_path; context fields stick at whatever was first
        # persisted, so YAML changes are silently ignored after the
        # first install. ``merge`` is the right choice for production
        # where runtime drift (changes made via API) needs to survive
        # restarts; ``run`` skips existing actions entirely. Override
        # via JVAGENT_UPDATE_MODE (see ``_jvagent_update_mode``).
        _update_mode = _jvagent_update_mode()
        # Clear any resident-action node orphaned by a forward-only class
        # rename BEFORE bootstrap — otherwise register_action collides on
        # the (agent_id, label) unique index and agentive startup fails.
        await _purge_dead_resident_action_orphans()
        await _jvagent_embed.bootstrap(
            app_root=_agent_root,
            update_mode=_update_mode,
            ensure_admin=False,  # integral owns its admin user; jvagent borrows the auth context
        )
        std_logging.getLogger("app.agentive").info(
            "jvagent embed bootstrap completed (app_root=%s, update_mode=%s)",
            _agent_root,
            _update_mode or "run",
        )
        # Say out loud that agent.yaml is NOT authoritative in this mode.
        #
        # The comment above has described this accurately since the setting
        # landed, and it still cost a full investigation to rediscover: the
        # symptom is that you edit agent.yaml, restart, see
        # "bootstrap completed (update_mode=merge)", and reasonably conclude
        # your change took — while the persisted node keeps its original
        # value. Nothing fails. The INFO line above is true and unhelpful,
        # because it reports the mode without its consequence.
        #
        # Measured instance: raising the orchestrator's observation_max_chars
        # from 4000 to 12000 in agent.yaml had no effect under merge; the
        # attribute had to be written onto the action node directly. That
        # change is worth 77% of the tokens on a listing question, so a
        # silently-dropped edit is not a cosmetic loss.
        #
        # Deliberately WARNING, not ERROR: merge is the CORRECT setting for
        # prod, where runtime drift from the API must survive restarts. The
        # problem is never that merge is on — only that it is invisible.
        if _update_mode == "merge":
            std_logging.getLogger("app.agentive").warning(
                "jvagent update_mode=merge: agent.yaml context values are "
                "applied only when an action is FIRST registered. Edits to "
                "already-registered actions (model, prompts, budgets, "
                "observation_max_chars, …) are silently ignored — the "
                "persisted node keeps its existing values. This is intended "
                "where runtime drift must survive restarts; use "
                "JVAGENT_UPDATE_MODE=source to make agent.yaml authoritative, "
                "at the cost of discarding that drift."
            )
        from app.agentive.skill_bundle_provider import (
            install_skill_provider_into_jvagent,
        )

        install_skill_provider_into_jvagent()
    else:
        std_logging.getLogger("app.agentive").warning(
            "%s/app.yaml not found; skipping jvagent embed",
            _agent_root,
        )

    configure_standard_logging(
        level=settings.LOG_LEVEL,
        enable_colors=True,
        preserve_handler_class_names=["DBLogHandler"],
    )

    if settings.DB_LOGGING_ENABLED:
        log_levels: set = set()
        for level_name in settings.DB_LOGGING_LEVELS.split(","):
            level_name = level_name.strip().upper()
            try:
                log_levels.add(getattr(std_logging, level_name))
            except AttributeError:
                pass
        if not log_levels:
            log_levels = {std_logging.ERROR, std_logging.CRITICAL}
        initialize_logging_database(
            database_name=settings.DB_LOGGING_DB_NAME,
            enabled=settings.DB_LOGGING_ENABLED,
            log_levels=log_levels,
            enable_api_endpoints=settings.DB_LOGGING_API_ENABLED,
        )
        # ChangeEvents live as DBLog rows in the logging database — ensure
        # DBLog indexes now that the logging DB is registered. Failure is
        # logged and swallowed so a misconfigured logging DB cannot block boot.
        from app.services.change_event_logger import get_change_event_logger

        await get_change_event_logger().ensure_indexes()

    # I-PERF-IDX: materialize jvspatial-default + per-class indexes for
    # every Integral Node/Edge model registered at boot. Class-level
    # annotations (`attribute(indexed=True)`, `@compound_index`,
    # `get_indexes()` overrides) live on the models; jvspatial's
    # `_ensured_indexes` registry makes the loop idempotent. Gated ONLY by
    # JVSPATIAL_AUTO_CREATE_INDEXES so serverless / cold-start deploys can
    # opt out — it used to sit under DB_LOGGING_ENABLED, so turning off DB
    # logging silently turned off every index. Failures are isolated
    # per-class so one bad index def doesn't block boot.
    if _auto_create_indexes_enabled():
        await _ensure_model_indexes()

    sep = "=" * 80
    print(sep)
    print("Integral Backend API starting...")
    print(sep)
    # Read from the resolved ServerConfig (post env_overrides merge) so
    # the startup banner reflects what jvspatial actually applied — not
    # just integral's Settings defaults.
    _cfg = server.config
    print(f"Database Type: {_cfg.database.db_type}")
    print(f"Database Path: {_cfg.database.db_path}")
    print(f"Authentication: {'Enabled (JWT)' if _cfg.auth.enabled else 'Disabled'}")
    print(f"CORS Origins: {_cfg.cors.cors_origins}")
    print(f"Log Level: {settings.LOG_LEVEL}")
    print(
        f"Database Logging: {'Enabled' if settings.DB_LOGGING_ENABLED else 'Disabled'}"
    )
    print(sep)
    print(f"Server running at http://{_cfg.host}:{_cfg.port}")
    print(f"API Documentation: http://{_cfg.host}:{_cfg.port}/docs")
    print(sep)

    # Phase 2 D-15 option (a) — spawn the ChangeEvent TTL reclaim loop.
    # The function-scope TESTING gate at the top of _startup means we already
    # short-circuit out of test runs before reaching this site, so the loop only
    # spawns in real (non-pytest) processes. Also gated on CHANGE_EVENT_ENABLED
    # so the kill switch disables snapshot reclamation alongside emission.
    if settings.CHANGE_EVENT_ENABLED:
        from app.services.change_event_ttl import ttl_reclaim_loop

        _background_tasks.append(asyncio.create_task(ttl_reclaim_loop()))
        std_logging.getLogger("app.services.change_event_ttl").info(
            "change_event_ttl: reclaim loop spawned at startup"
        )
    else:
        std_logging.getLogger("app.services.change_event_ttl").info(
            "change_event_ttl: reclaim loop skipped (CHANGE_EVENT_ENABLED=False)"
        )

    # Phase 30 (DR-30-01 + DR-30-02) — rehydrate per-workspace bundle
    # tool + hook registry from every active App. The registry is
    # in-process; without this, restart clears all bindings until the
    # next install_app call. Best-effort: failure is logged + swallowed
    # so a single broken bundle never blocks server boot.
    try:
        from app.services.hooks.install_hook import rehydrate_all_installed_bundles

        await rehydrate_all_installed_bundles()
        std_logging.getLogger("app.services.hooks.install_hook").info(
            "hook framework: rehydrated installed bundles at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.services.hooks.install_hook").warning(
            "hook framework rehydration failed: %s", _exc
        )

    # ADR-009 — rehydrate MCP-mounted connector tools into the in-process
    # workspace registry (same hot-registration class as bundle finalize_install).
    try:
        from app.agentive.connectors.mcp_mount import rehydrate_mcp_connectors

        await rehydrate_mcp_connectors()
        std_logging.getLogger("app.agentive.connectors.mcp_mount").info(
            "mcp connectors: rehydrated at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.agentive.connectors.mcp_mount").warning(
            "mcp connector rehydration failed: %s", _exc
        )

    # Phase 5 Plan 05-03 — connector sync_loop spawn (locked decision #8).
    # Mirrors the ttl_reclaim_loop precedent immediately above; inherits the
    # function-scope TESTING gate at L137 (no extra check needed here — we
    # already short-circuited if PYTEST_CURRENT_TEST or TESTING was set).
    # Locked decision §Q9 — runs UNCONDITIONALLY of AGENTIVE_ENABLED; the
    # loop body is a cheap no-op when no Connectors are registered
    # (I-SYNC-02). Wrapped in try/except so a sync_scheduler import failure
    # never blocks server startup.
    try:
        from app.services.connectors.sync_scheduler import sync_loop

        _background_tasks.append(asyncio.create_task(sync_loop()))
        std_logging.getLogger("app.services.connectors.sync_scheduler").info(
            "connector sync_loop spawned at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.services.connectors.sync_scheduler").warning(
            "connector sync_loop spawn failed: %s", _exc
        )

    # Phase 7 Plan 07-04 — approval TTL reclaim loop.
    # Mirrors the ttl_reclaim_loop / sync_loop precedents above; inherits
    # the function-scope TESTING gate at L138 (already short-circuited if
    # PYTEST_CURRENT_TEST or TESTING was set). The loop body is a cheap
    # no-op when no Approval rows are pending. Wrapped in try/except so
    # an approval_ttl import failure never blocks startup.
    try:
        from app.services.approval_ttl import approval_ttl_loop

        _background_tasks.append(asyncio.create_task(approval_ttl_loop()))
        std_logging.getLogger("app.services.approval_ttl").info(
            "approval_ttl: loop spawned at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.services.approval_ttl").warning(
            "approval_ttl: loop spawn failed: %s", _exc
        )

    # Phase 10 Plan 10-05 — App install reaper (APP-LIFECYCLE-01).
    # Sweeps Apps in lifecycle_state="awaiting_settings" whose install
    # token has expired (TTL configurable via APP_INSTALL_TOKEN_TTL_HOURS,
    # default 1h), calls uninstall_app(force=True), and emits a single
    # app.force_uninstalled ChangeEvent with details.reason="settings_pause_timeout".
    # Mirrors the approval_ttl_loop precedent above; inherits the
    # function-scope TESTING gate at L138 (already short-circuited if
    # PYTEST_CURRENT_TEST or TESTING was set). Wrapped in try/except so a
    # reaper import failure never blocks startup. Migrating to the native
    # scheduler when it ships is a one-line change (replace start_reaper
    # with the scheduler primitive — same gate as Plan 10-04
    # uplink_registry._scheduler_available).
    try:
        from app.services.app_install_reaper import start_reaper

        _task = start_reaper()
        if _task is not None:
            _background_tasks.append(_task)
        std_logging.getLogger("app.services.app_install_reaper").info(
            "app_install_reaper: loop spawned at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.services.app_install_reaper").warning(
            "app_install_reaper: loop spawn failed: %s", _exc
        )

    # Routine Tasks — user-issued recurring chat instructions, replayed as an
    # agent turn on a cron cadence (app/services/routine_task_scheduler.py).
    # Fourth instance of the ttl_reclaim_loop / sync_loop / approval_ttl_loop /
    # app_install_reaper asyncio.create_task pattern — see that module's
    # docstring for why jvspatial's own thread-based SchedulerService was
    # evaluated and not used. Mirrors the app_install_reaper precedent above;
    # inherits the function-scope TESTING gate (already short-circuited if
    # PYTEST_CURRENT_TEST or TESTING was set). Wrapped in try/except so an
    # import failure never blocks startup. This is also what backs
    # app.services.scheduler.scheduler_available() — see that module's
    # docstring for the App-bundled-agent degradation-path unlock.
    try:
        from app.services.routine_task_scheduler import start_scheduler

        _task = start_scheduler()
        if _task is not None:
            _background_tasks.append(_task)
        std_logging.getLogger("app.services.routine_task_scheduler").info(
            "routine_task_scheduler: loop spawned at startup"
        )
    except Exception as _exc:  # noqa: BLE001
        std_logging.getLogger("app.services.routine_task_scheduler").warning(
            "routine_task_scheduler: loop spawn failed: %s", _exc
        )

    # Durable work kernel — recovery + leased worker + event consumer.
    # Runs after indexes (already ensured above) and beside the routine
    # producer loop. Production fails closed on Mongo / missing txn CAS.
    try:
        from app.agentive.services.work_lifecycle import start_work_kernel_background

        await start_work_kernel_background(_background_tasks)
        std_logging.getLogger("app.agentive.services.work_lifecycle").info(
            "work_kernel: recovery + worker + event loops spawned"
        )
    except Exception as _exc:  # noqa: BLE001
        _dev = bool(settings.DEBUG)
        if not _dev:
            raise
        std_logging.getLogger("app.agentive.services.work_lifecycle").warning(
            "work_kernel: startup skipped in DEBUG: %s", _exc
        )


async def _unify_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": exc.errors(),
        },
    )


async def _shutdown() -> None:
    """Log shutdown message and close database connections to avoid hang on exit."""
    if not (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")):
        print("Integral Backend API shutting down...")

    # Cancel every background loop spawned at startup BEFORE closing the
    # DB pool. Without this, a loop tick can fire while the pool is
    # draining and surface as "pool is closing / pool is closed"
    # warnings (most noticeable on Postgres where asyncpg's pool exposes
    # an explicit closing state).
    for _task in _background_tasks:
        if not _task.done():
            _task.cancel()
    if _background_tasks:
        # Drain with a short ceiling so a misbehaving loop can't hold
        # shutdown indefinitely (uvicorn's timeout_graceful_shutdown=5
        # would kill us anyway).
        try:
            await asyncio.wait_for(
                asyncio.gather(*_background_tasks, return_exceptions=True),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            std_logging.getLogger(__name__).warning(
                "shutdown: background tasks did not finish within 3s; "
                "proceeding to DB close"
            )
    _background_tasks.clear()

    # Stop background services started by the embedded jvagent runtime.
    try:
        from jvagent import embed as _jvagent_embed

        await _jvagent_embed.shutdown()
    except Exception:
        pass

    # Close ALL registered database connections (prime + extras such as the
    # jvspatial logging DB), not just prime.
    #
    # SQLite via aiosqlite runs each connection on a NON-DAEMON worker thread
    # (``_connection_worker_thread``). Any connection left open strands that
    # thread, so the interpreter blocks in ``threading._shutdown()`` joining it
    # and the process only dies on a SECOND CTRL+C. Close every registered DB
    # (prime + logging + extras) so no aiosqlite worker thread is stranded.
    # Iterate
    # every registered database via the public manager API, dedup by identity
    # (prime is registered under multiple keys), and close best-effort so one
    # failure can't strand the rest.
    try:
        import inspect as _inspect

        from jvspatial.db import get_database_manager

        _mgr = get_database_manager()
        _seen: set[int] = set()
        for _db_name in list(_mgr.list_databases().keys()):
            try:
                _db = _mgr.get_database(_db_name)
            except Exception:
                continue
            if _db is None or id(_db) in _seen:
                continue
            _seen.add(id(_db))
            _close = getattr(_db, "close", None)
            if _close is None:
                continue
            try:
                _result = _close()
                if _inspect.isawaitable(_result):
                    await _result
            except Exception:
                pass
    except Exception:
        pass


# Import API modules so @endpoint decorators register with the server.
importlib.import_module("app.api")

# Plan 06-05 — agentive HTTP routes register via @endpoint side-effect imports.
# Import the agentive surface BEFORE server.get_app() so jvspatial's
# discovery_service picks them up at app-creation time (mirrors core api
# discovery above).
importlib.import_module("app.agentive.api")

if settings.DB_LOGGING_ENABLED and settings.DB_LOGGING_API_ENABLED:
    importlib.import_module("jvspatial.logging.endpoints")

from app.api import agent_preferences as _agent_preferences_endpoint  # noqa: E402, F401

# Plan 06-05 — ai_chat, policies, conflicts, connectors (core sync) all moved
# to @endpoint registration via the side-effect importlib.import_module("app.api")
# call above. The previous `from … import router as …` shims are gone. The
# events_ws WebSocket route remains the lone explicit include below (WebSocket
# is the documented carve-out from @endpoint per CLAUDE.md § Forbidden Patterns
# Pragmatism Clause — jvspatial's @endpoint does not support WebSocket).
from app.api.events_ws import router as events_ws_router  # noqa: E402

# Server configuration follows the jvagent pattern (see
# ``jvagent/cli/server_config.py``): read JVSPATIAL_* env vars directly
# via ``jvspatial.env.env``, build typed config-group objects, and pass
# them to ``Server(...)``. No legacy-name bridge, no setdefault shims —
# the env var IS the source of truth, and integral's defaults appear
# only as the ``default=`` argument to each ``env()`` call.

# Integral's default CORS origins. jvspatial's built-in dev list omits
# :9006 (integral's frontend port).
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:9006",
    "http://127.0.0.1:9006",
]

# Resolve db_path to absolute so it works regardless of cwd (e.g. npm run
# dev from project root). Reads JVSPATIAL_DB_PATH directly. Only used for
# file-backed backends (json / sqlite); ignored for postgres / mongodb.
_db_type = env("JVSPATIAL_DB_TYPE", default="postgres")
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_path_raw = env("JVSPATIAL_DB_PATH", default="integral.db")
if not os.path.isabs(_db_path_raw):
    _db_path_raw = os.path.join(_backend_dir, _db_path_raw)
_db_path = os.path.abspath(_db_path_raw)
# Canonicalize only for backends that consume a filesystem path. Mutating this
# variable under Postgres/Mongo needlessly reintroduces file-store state and
# leaks across isolated test contexts.
if str(_db_type).strip().lower() in ("json", "sqlite"):
    os.environ["JVSPATIAL_DB_PATH"] = _db_path

# File-backed log stores only (json / sqlite). Postgres and other network
# backends use JVSPATIAL_POSTGRES_DSN / JVSPATIAL_LOG_POSTGRES_DSN.
if settings.LOG_DB_TYPE in ("json", "sqlite"):
    _log_db_path_raw = env("JVSPATIAL_LOG_DB_PATH", default="integral_logs.db")
    if not os.path.isabs(_log_db_path_raw):
        _log_db_path_raw = os.path.join(_backend_dir, _log_db_path_raw)
    os.environ["JVSPATIAL_LOG_DB_PATH"] = os.path.abspath(_log_db_path_raw)
os.environ["JVSPATIAL_LOG_DB_TYPE"] = settings.LOG_DB_TYPE


def _ensure_postgres_dsn() -> None:
    """Compose ``JVSPATIAL_POSTGRES_DSN`` from ``POSTGRES_*``.

    Swarm compose builds a DSN string without URL-encoding (passwords with
    ``@``, ``:``, ``/``, etc. break parsing). We always rebuild from the
    discrete ``POSTGRES_*`` vars when ``POSTGRES_PASSWORD`` is present so the
    password Postgres receives matches what the ``db`` service was configured
    with at deploy time.
    """
    from urllib.parse import quote_plus

    user = os.environ.get("POSTGRES_USER", "integral").strip()
    password = (os.environ.get("POSTGRES_PASSWORD") or "integral").strip()
    database = os.environ.get("POSTGRES_DB", "integral_dev").strip()
    host = os.environ.get("POSTGRES_HOST", "db").strip()
    port = os.environ.get("POSTGRES_PORT", "5432").strip()

    current = os.environ.get("JVSPATIAL_POSTGRES_DSN", "").strip()
    if current and not (os.environ.get("POSTGRES_PASSWORD") or "").strip():
        # External/managed Postgres: caller set JVSPATIAL_POSTGRES_DSN only.
        if current.startswith("$") or "${" in current:
            raise RuntimeError(
                "JVSPATIAL_POSTGRES_DSN looks unexpanded; set POSTGRES_* or a "
                "literal DSN without ${…} placeholders."
            )
        return

    os.environ["JVSPATIAL_POSTGRES_DSN"] = (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )
    print(
        "Integral DB bootstrap: "
        f"type=postgres host={host} port={port} db={database} user={user} "
        f"password_set={bool(password)} password_len={len(password)} "
        f"dsn_source={'env-dsn' if current else 'postgres-env'}",
        flush=True,
    )


# jvspatial's Server-level DatabaseConfigurator covers json / sqlite /
# mongodb / dynamodb but does NOT yet have a Postgres branch (the
# Postgres backend itself ships via PostgresDB / db.factory in Phase C,
# 2026-05-26). Bootstrap PG via the factory ourselves before Server()
# construction, register it as the prime DB, and tell Server to skip
# its own DB init by passing db_type=None.
if _db_type in ("postgres", "postgresql"):
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.factory import create_database
    from jvspatial.db.manager import (
        DatabaseManager,
        get_database_manager,
        set_database_manager,
    )

    from app.services.db_init import observability_kwargs

    _ensure_postgres_dsn()
    _prime_db = create_database(
        "postgres",
        dsn=os.environ["JVSPATIAL_POSTGRES_DSN"],
        **observability_kwargs(),
    )
    try:
        _mgr = get_database_manager()
        _mgr.set_prime_database(_prime_db)
    except (RuntimeError, AttributeError):
        _mgr = DatabaseManager(prime_database=_prime_db)
        set_database_manager(_mgr)
    set_default_context(GraphContext(database=_mgr.get_current_database()))
    _server_db_type = None  # skip Server's own DatabaseConfigurator
else:
    _server_db_type = _db_type

# jvagent's embed ``Server.get_app()`` mounts jvagent's own admin / memory /
# graph / logs routes on this app unless ``JVAGENT_EMBED_ENDPOINTS_DISABLED``
# is truthy (see ``jvagent/embed/bootstrap.py::_should_register_endpoints``).
# Integral never uses them, and the ``auth=False`` interact routes register
# through the same path. Off unless explicitly opted in via Settings.
# Use setdefault so an explicit env (and pytest's env-leak guard) is preserved.
if not settings.JVAGENT_EMBED_ENDPOINTS_ENABLED:
    os.environ.setdefault("JVAGENT_EMBED_ENDPOINTS_DISABLED", "1")

server = Server(
    title="Integral API",
    description="Backend API for Integral — unified tracks, apps, and collaboration — powered by jvspatial",
    version="0.1.0",
    host=env("JVSPATIAL_HOST", default="0.0.0.0"),
    port=int(env("JVSPATIAL_PORT", default=4000)),
    database=DatabaseConfig(
        db_type=_server_db_type,
        db_path=_db_path,
        db_database_name=env("JVSPATIAL_MONGODB_DB_NAME", default=None),
    ),
    auth=AuthConfig(
        enabled=env("JVSPATIAL_AUTH_ENABLED", default=True, parse=parse_bool),
        # test_mode=True tells jvspatial's AuthMiddleware to honour pre-set
        # request.state.user from TestAuthBypassMiddleware without forcing
        # re-authentication. Required for pytest in-process ASGI testing as
        # of jvspatial ≥0.0.3 (Request State Contract — auth_middleware.py).
        test_mode=bool(os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST")),
        jwt_secret=settings.SECRET_KEY,
        jwt_algorithm=settings.ALGORITHM,
        jwt_expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        refresh_token_rotation=settings.REFRESH_TOKEN_ROTATION,
        registration_open=settings.REGISTRATION_OPEN,
        # integral owns /auth/forgot-password + /auth/reset-password (custom
        # anti-enumeration flow in app/api/auth.py); jvspatial's built-in
        # reset endpoints are suppressed by default to avoid duplicate
        # operation IDs. Flip via INTEGRAL_AUTH_PASSWORD_RESET_ENABLED=1
        # only if integral's custom flow is removed first.
        password_reset_enabled=env(
            "INTEGRAL_AUTH_PASSWORD_RESET_ENABLED",
            default=False,
            parse=parse_bool,
        ),
        # OAuth-secured MCP server (M2b Task 1). Enabling these turns jvspatial's
        # M1 OAuth machinery on for the whole app: the Authorization Server
        # auto-mounts /api/oauth/{authorize,token,register,revoke} + the root
        # /.well-known/{oauth-authorization-server,oauth-protected-resource} +
        # jwks.json discovery documents (auto-exempt from bearer auth), and the
        # Resource-Server path accepts OAuth bearers on auth=True endpoints and
        # emits an RFC 9728 WWW-Authenticate challenge on 401. The future
        # /api/mcp surface stays auth-gated — it is NOT exempt below.
        oauth_enabled=True,
        accept_oauth_bearer=True,
        oauth_issuer_url=settings.OAUTH_ISSUER_URL,
        oauth_supported_scopes=settings.OAUTH_SUPPORTED_SCOPES,
        # M3a — when an unauthenticated browser hits GET /api/oauth/authorize,
        # 302-redirect it to the Integral SPA's consent route (the SPA then
        # drives consent via the bearer-authed /api/oauth/consent endpoints).
        # The target is ONLY the configured trusted origin + the original OAuth
        # query string jvspatial appends — never a request-derived host — so it
        # cannot be turned into an open redirect.
        oauth_authorize_login_redirect=(
            settings.FRONTEND_ORIGIN.rstrip("/") + "/oauth/authorize"
        ),
        exempt_paths=[
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
            # NOTE: /api/auth/register is deliberately absent — jvspatial's
            # built-in registration route is removed from the auth router
            # below (see ``disable_auth_endpoint``). It only ever created an
            # ``AuthUser``, leaving a usable login with no ``User`` node, no
            # Personal Workspace, no verification OTP and no personal-context
            # App. ``/api/auth/signup`` is the only supported signup path.
            "/api/auth/login",
            "/api/auth/refresh",
            "/api/auth/signup",
            # Password reset flow — anti-enumeration response means we
            # accept these without a user session. Defence-in-depth: the
            # endpoints themselves declare auth=False.
            "/api/auth/forgot-password",
            "/api/auth/reset-password",
            # Service key only (X-Integral-Service-Key); not user JWT
            "/api/agentive/uplink/register-system",
            "/api/agentive/uplink/heartbeat-system",
            *_AUTH_EXEMPT_PATHS_EXTRA,
        ],
    ),
    cors=CORSConfig(
        cors_enabled=env("JVSPATIAL_CORS_ENABLED", default=True, parse=parse_bool),
        cors_origins=env(
            "JVSPATIAL_CORS_ORIGINS",
            default=list(_DEFAULT_CORS_ORIGINS),
            parse=parse_csv,
        ),
        cors_methods=env("JVSPATIAL_CORS_METHODS", default=["*"], parse=parse_csv),
        cors_headers=env(
            "JVSPATIAL_CORS_HEADERS",
            default=[
                "Content-Type",
                "Authorization",
                "X-API-Key",
                "X-Integral-Scope",
            ],
            parse=parse_csv,
        ),
    ),
    node_types=[
        IntegralApp,
        Users,
        Workspaces,
        ContentProfiles,
        Invitations,
        Views,
        Dashboards,
        ContentProfile,
        User,
        Workspace,
        Apps,
        Tracks,
        ChatThreads,
        Invitation,
        App,
        Track,
        Entry,
        EntryType,
        Comment,
        Attachment,
        Tag,
        View,
        Dashboard,
        Notification,
        # ChangeEvent is no longer a prime-graph Node — it is persisted as a
        # DBLog row with log_level="CHANGE_EVENT" in the logging database (see
        # app/services/change_event_logger.py). DBLog is registered by
        # jvspatial.logging.initialize_logging_database, not here.
        # Phase 3 D-02 — Policy is core authorization infrastructure.
        # Registered ALWAYS (not gated on AGENTIVE_ENABLED): admins may attach
        # a Policy to a User for compliance even with the agentive layer off.
        Policy,
        # AI chat persistence (initiative: ai-chat). Registered always —
        # chat is a first-class core feature, not gated on AGENTIVE_ENABLED.
        ChatThread,
        ChatMessage,
        # Phase 5 Plan 05-03 — connector-sync conflict record. Core
        # (NOT gated on AGENTIVE_ENABLED) per locked decision #12: the
        # Conflict REST surface ships unconditionally so non-agentive
        # deployments that still surface connector data can list /
        # resolve conflicts.
        Conflict,
        # Phase 7 Plan 07-04 — pending-agent-write record. Core (NOT gated
        # on AGENTIVE_ENABLED) per CONTEXT lock #12. The pending-write
        # review surface ships unconditionally so non-agentive deployments
        # that still attach Policies with ``requires_human_approval=True``
        # to agent uplinks (e.g. compliance) can list / approve / reject.
        Approval,
        # Phase 10 Plan 10-04 (APP-SKILLS-01) — App-bundled Skill records.
        # Registered ALWAYS so the install lifecycle (Plan 10-05) can
        # materialize Skills as part of merge-time and install-time
        # transactions without depending on AGENTIVE_ENABLED.
        Skill,
    ],
    on_startup=[_startup],
    on_shutdown=[_shutdown],
)

# Remove jvspatial's built-in POST /api/auth/register before the auth router
# is mounted. That route registers an ``AuthUser`` only; it does not create the
# Integral ``User`` graph node, the Personal Workspace, the verification OTP or
# the personal-context App that ``/api/auth/signup`` provisions. With
# REGISTRATION_OPEN defaulting True, anyone could mint a half-provisioned
# account that logs in fine but has no workspace. Integral has exactly one
# supported signup path, so the unsupported one is not exposed at all.
# Must run before ``get_app()`` — that is when the auth router is included.
if not server.disable_auth_endpoint("/register"):  # pragma: no cover - boot guard
    raise RuntimeError(
        "Failed to disable jvspatial's built-in /auth/register route; "
        "refusing to boot with an unsupported registration surface exposed."
    )

app = server.get_app()
app.add_exception_handler(RequestValidationError, _unify_request_validation_error)

_JVAGENT_INTERACT_ROUTE_RE = re.compile(r"^/api/agents/[^/]+/interact(?:/|$)")


def _jvagent_interact_routes(target_app: Any) -> list[str]:
    """Paths on ``target_app`` that expose jvagent's raw interact surface.

    jvagent's ``/agents/{id}/interact*`` endpoints are ``auth=False`` and
    bypass Integral's principal / workspace / staging path entirely. They
    are not mounted today, but they live in the same registration path as
    the admin routes, one import away. Boot refuses if any appear.
    """
    found: list[str] = []
    for route in getattr(target_app, "routes", []) or []:
        path = getattr(route, "path", "") or ""
        if _JVAGENT_INTERACT_ROUTE_RE.match(path):
            found.append(path)
    return found


_leaked_interact_routes = _jvagent_interact_routes(app)
if _leaked_interact_routes:
    raise RuntimeError(
        "jvagent's unauthenticated interact routes are mounted on the Integral "
        f"app: {_leaked_interact_routes}. The resident is reachable through "
        "/api/chat and MCP only; set JVAGENT_EMBED_ENDPOINTS_DISABLED=1 (the "
        "default when JVAGENT_EMBED_ENDPOINTS_ENABLED is false)."
    )

# EVT-01: register the core /api/events WS router. Per D-13 this is core
# infrastructure — registered BEFORE the AGENTIVE_ENABLED block so /api/events
# is available regardless of agentive availability. Plan 06-05: WebSocket is
# the documented carve-out from @endpoint (jvspatial framework limitation);
# events_ws_router is still an explicit include.
app.include_router(events_ws_router)

# Plan 06-05 — ai_chat, policies, conflicts, connectors (core sync) routes all
# self-register via @endpoint side-effect import in app.api.__init__.py. No
# explicit include_router needed; the previous shims have been removed.

# Register the default chat backend(s). The router never imports a harness
# module directly — everything routes through this registry. To swap or add
# a harness: implement ChatBackendProvider, register it here.
from app.services.chat_providers import get_registry  # noqa: E402
from app.services.chat_providers.jvagent_provider import (  # noqa: E402
    jvagent_provider,
)

get_registry().register(jvagent_provider, default=True)


# ---------------------------------------------------------------------------
# Agentive Layer (always on)
# ---------------------------------------------------------------------------

from app.agentive.api import register_routes
from app.agentive.api.errors import install_agentive_error_handlers  # D-03
from app.agentive.nodes import (
    AgentConfig,
    ChannelIdentity,
    Connector,
    ConversationContext,
    RoutineTask,
)

for node_cls in (
    ChannelIdentity,
    ConversationContext,
    AgentConfig,
    Connector,
    RoutineTask,
):
    server.add_node_type(node_cls)

# Pass the core handler explicitly: this call REPLACES the registration made
# above (Starlette keys handlers by exception class), so non-agentive paths can
# only reach the core envelope by delegation, not by re-raising.
install_agentive_error_handlers(app, fallback=_unify_request_validation_error)
register_routes(app)

# PerfHeader must be the INNERMOST middleware (added first): every
# BaseHTTPMiddleware above it runs its downstream in a child anyio task with
# a contextvar COPY, so ObservableDatabase's db_op_counter increments made in
# the endpoint task are only visible to middleware sharing that task. With no
# BaseHTTPMiddleware below it, the counter reads true.
from app.middleware.perf_header import PerfHeaderMiddleware

app.add_middleware(PerfHeaderMiddleware)

if os.getenv("INTEGRAL_SERVICE_KEY"):
    from app.agentive.middleware.service_auth import ServiceAuthMiddleware

    app.add_middleware(ServiceAuthMiddleware)

from app.agentive.sample_consumers import register_sample_consumers

register_sample_consumers()

if os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST"):
    from app.middleware.test_auth import TestAuthBypassMiddleware

    app.add_middleware(TestAuthBypassMiddleware)

from app.middleware.charset_utf8 import CharsetUTF8Middleware
from app.middleware.permissions_cache import PermissionsCacheMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.sentry_init import init_sentry_if_configured

# Order note: Starlette runs middleware in REVERSE order of addition for
# requests, and forward order for responses. We want SecurityHeaders to
# run last on response (so its headers are not stripped by anything
# downstream) — that means it should be added FIRST. RateLimit needs to
# short-circuit before any heavy auth/permission work, so it's added
# last (runs first on request).
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PermissionsCacheMiddleware)
app.add_middleware(CharsetUTF8Middleware)
app.add_middleware(RateLimitMiddleware)

# ---------------------------------------------------------------------------
# MCP Streamable-HTTP transport (M2b Task 3)
# ---------------------------------------------------------------------------
#
# Mount the M2a tool catalogue as an OAuth-secured MCP server at /api/mcp. The
# mount lands AFTER the middleware stack above so every Starlette middleware
# (security headers, permissions cache, rate limit, auth — and, under TESTING,
# TestAuthBypassMiddleware) wraps the MCP sub-app: /api/mcp stays auth-gated
# exactly like every other /api/* route (it is deliberately NOT in
# auth_exempt_paths). The MCP server itself reads identity from
# request.state.user via resolve_principal_id; the surrounding middleware is
# what populates it.
#
# StreamableHTTPSessionManager.handle_request raises
# "RuntimeError: Task group is not initialized" unless mgr.run() has been
# entered exactly once (it creates its anyio task group lazily inside run();
# stateless=True still requires it). The legacy MCP adapter omitted this and hit
# that error. We drive run() for the full app lifetime by registering a startup
# hook that enters the run() context manager and a shutdown hook that exits it.
# Both hooks fire inside the single FastAPI lifespan jvspatial installs
# (LifecycleManager.lifespan -> startup()/shutdown()), so the anyio task group
# is entered and exited within the same task on the serving event loop — the
# correct anyio discipline for a task group that must outlive a single request.
# (The hooks are registered unconditionally, NOT gated on TESTING, so any
# lifespan-running client — e.g. uvicorn, or asgi-lifespan's LifespanManager in
# tests — gets an initialized manager.)
#
# anyio gotcha: a StreamableHTTPSessionManager instance's run() may be entered
# only ONCE — it sets _has_started and refuses re-entry. In production a process
# has exactly one lifespan cycle, so the default-built instance is used as-is.
# But a test session that opens several LifespanManager(app) blocks would re-run
# startup against the same spent instance. We guard for that by rebuilding a
# fresh manager on each startup whenever the current one is already started, and
# routing the mounted ASGI entrypoint at the *current* active manager (held in a
# one-element list so the closure always sees the live instance).
from app.agentive.mcp.server import build_session_manager  # noqa: E402

_mcp_server, _mcp_session_manager = build_session_manager()

# Active manager handle — a mutable holder so the mounted ASGI closure and the
# shutdown hook always reach the instance startup last entered run() on.
_mcp_active = [_mcp_session_manager]

# Holds the entered run() context manager so the shutdown hook can exit it.
_mcp_run_cm = None


async def _mcp_asgi(scope, receive, send):
    """ASGI entrypoint for the mounted MCP transport.

    Dispatches at the currently-active session manager (rebuilt per lifespan
    cycle), so handle_request always targets the instance whose task group the
    startup hook initialized.
    """
    await _mcp_active[0].handle_request(scope, receive, send)


async def _start_mcp_session_manager() -> None:
    """Enter the MCP session-manager run() context at app startup.

    Initializes the anyio task group handle_request depends on. Rebuilds a fresh
    manager if the current one already ran (run() is single-use), then enters
    its run() and records the active instance + the context manager so the
    shutdown hook can exit the same one.
    """
    global _mcp_run_cm, _mcp_server, _mcp_session_manager
    mgr = _mcp_active[0]
    if getattr(mgr, "_has_started", False):
        _mcp_server, _mcp_session_manager = build_session_manager()
        mgr = _mcp_session_manager
        _mcp_active[0] = mgr
    _mcp_run_cm = mgr.run()
    await _mcp_run_cm.__aenter__()


async def _stop_mcp_session_manager() -> None:
    """Exit the MCP session-manager run() context at app shutdown."""
    global _mcp_run_cm
    if _mcp_run_cm is not None:
        try:
            await _mcp_run_cm.__aexit__(None, None, None)
        finally:
            _mcp_run_cm = None


app.mount("/api/mcp", _mcp_asgi)
server.lifecycle_manager.add_startup_hook(_start_mcp_session_manager)
server.lifecycle_manager.add_shutdown_hook(_stop_mcp_session_manager)

# Optional Sentry — no-op when SENTRY_DSN is unset.
if not (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")):
    init_sentry_if_configured()

if __name__ == "__main__":
    # Disable hot reload when using SQLite — file changes on DB access trigger restarts
    reload_enabled = settings.DEBUG
    workers = 1 if reload_enabled else max(settings.WORKERS, 1)
    server.run(
        app_path="app.main:app",
        reload=reload_enabled,
        timeout_graceful_shutdown=5,
        workers=workers,
    )
