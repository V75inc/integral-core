"""Database initialization helper for CLI scripts and backfills."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from jvspatial.db.manager import (
    DatabaseManager,
    get_database_manager,
    set_database_manager,
)


def observability_kwargs() -> dict:
    """Resolve ObservableDatabase wrapper kwargs from env.

    Mirrors jvspatial's ``DatabaseConfigurator._resolve_observability_kwargs``
    for the bootstrap paths where integral calls ``create_database`` directly
    (postgres in ``app.main``, CLI scripts here). Without this the
    ``db_op_counter`` behind ``X-DB-Round-Trip-Count`` never increments on
    those paths.
    """
    raw_enabled = str(os.environ.get("JVSPATIAL_OBSERVABILITY_ENABLED", "")).lower()
    if raw_enabled == "":
        # Default on for deploy stacks that set JVSPATIAL_DB_TYPE=postgres.
        db_type = (os.environ.get("JVSPATIAL_DB_TYPE") or "").strip().lower()
        observe = db_type in ("postgres", "postgresql")
    else:
        observe = raw_enabled in ("1", "true", "yes", "on")
    slow_raw = os.environ.get("JVSPATIAL_SLOW_QUERY_MS", "")
    try:
        slow_query_ms = float(slow_raw) if slow_raw != "" else 100.0
    except ValueError:
        slow_query_ms = 100.0
    kwargs: dict = {"observe": observe, "slow_query_ms": slow_query_ms}
    cache_raw = os.environ.get("JVSPATIAL_CACHE_GET_SIZE", "").strip()
    if cache_raw:
        try:
            kwargs["cache_get_size"] = int(cache_raw)
        except ValueError:
            pass
    if observe:
        from app.services.db_metrics import RequestDBOpRecorder

        kwargs["metrics"] = RequestDBOpRecorder()
    return kwargs


def init_prime_db() -> None:
    """Initialize the prime database context based on environment variables.

    Allows CLI scripts and backfills to query and write to the database (supporting
    postgres, mongodb, sqlite, and json backends) without running a full server lifecycle.
    """
    db_type = (os.environ.get("JVSPATIAL_DB_TYPE") or "").strip().lower()
    if not db_type:
        return

    obs_kwargs = observability_kwargs()

    if db_type in ("postgres", "postgresql"):
        if not os.environ.get("JVSPATIAL_POSTGRES_DSN"):
            user = os.environ.get("POSTGRES_USER", "integral").strip()
            password = (os.environ.get("POSTGRES_PASSWORD") or "integral").strip()
            database = os.environ.get("POSTGRES_DB", "integral_dev").strip()
            host = os.environ.get("POSTGRES_HOST", "db").strip()
            port = os.environ.get("POSTGRES_PORT", "5432").strip()
            os.environ["JVSPATIAL_POSTGRES_DSN"] = (
                f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
                f"@{host}:{port}/{database}"
            )
        prime_db = create_database(
            "postgres",
            dsn=os.environ["JVSPATIAL_POSTGRES_DSN"],
            **obs_kwargs,
        )
    elif db_type == "mongodb":
        uri = os.environ.get("JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.environ.get("JVSPATIAL_MONGODB_DB_NAME", "integral")
        prime_db = create_database(
            "mongodb",
            uri=uri,
            database_name=db_name,
            **obs_kwargs,
        )
    elif db_type in ("sqlite", "json"):
        backend_dir = Path(__file__).resolve().parents[2]
        db_path_raw = os.environ.get("JVSPATIAL_DB_PATH", "integral.db")
        if not os.path.isabs(db_path_raw):
            db_path_raw = os.path.join(backend_dir, db_path_raw)
        db_path = os.path.abspath(db_path_raw)
        prime_db = create_database(
            db_type,
            path=db_path,
            **obs_kwargs,
        )
    else:
        prime_db = create_database(db_type, **obs_kwargs)

    try:
        mgr = get_database_manager()
        mgr.set_prime_database(prime_db)
    except (RuntimeError, AttributeError):
        mgr = DatabaseManager(prime_database=prime_db)
        set_database_manager(mgr)
    set_default_context(GraphContext(database=mgr.get_current_database()))
