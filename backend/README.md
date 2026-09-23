# Integral backend

Python FastAPI backend for the Integral workspace platform, powered by jvspatial.

## 🚀 Quick Start

### Prerequisites

- Python 3.9 or higher
- pip (Python package manager)
- Virtual environment (recommended)

**Resumable chunked uploads (Plan 03 — Phase 6):**

For files larger than the single-shot ceiling (`ATTACHMENT_MAX_UPLOAD_BYTES`,
default 500 MB), the frontend's `smartUploadForEntry` automatically
switches to the resumable chunked path. The path is gated by:

- `CHUNKED_UPLOAD_ENABLED=true` — required to enable the endpoints
- `CHUNKED_UPLOAD_CHUNK_SIZE_BYTES` — default 8 MiB
- `CHUNKED_UPLOAD_MAX_TOTAL_BYTES` — default 5 GiB per session
- `CHUNKED_UPLOAD_SESSION_TTL_HOURS` — default 24

Sessions can be resumed by issuing `GET /uploads/{session_id}` and
continuing from `received_bytes`. Cancel with `DELETE /uploads/{session_id}`.
A periodic sweep job (call `expire_stale_sessions` from your scheduler
of choice) reclaims storage from abandoned sessions.

**System libraries (attachment handling, Plan 03):**

The attachment metadata pipeline soft-imports its third-party libs and
their underlying system dependencies, so any missing piece degrades
gracefully (extraction marks `status=partial` and the upload still
succeeds). For full coverage, install:

- **libmagic** — required for server-side MIME sniffing.
  - macOS: `brew install libmagic`
  - Debian/Ubuntu: `apt-get install libmagic1`
  - Windows: optional and **off by default**. The backend blocks `python-magic`
    at import time on Windows because a broken `libmagic` DLL can crash the
    process before Python starts. Set `ATTACHMENT_LIBMAGIC_ENABLED=1` only
    when a working libmagic build is installed.
- **ffprobe** (part of ffmpeg) — required for video metadata.
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `apt-get install ffmpeg`
- **libreoffice** (headless) — used by the pptx → PDF preview path
  (Phase 2). Without it, pptx files fall back to "download to view".
  - macOS: `brew install --cask libreoffice`
  - Debian/Ubuntu: `apt-get install libreoffice`

### Installation

**Prerequisite: [uv](https://docs.astral.sh/uv/).** This project resolves and
installs through `uv` and `uv.lock`; plain `pip` bypasses the lockfile and will
resolve different versions.

```bash
brew install uv                              # macOS
curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux / macOS without brew
```

1. **Install dependencies**

   `uv` creates `backend/.venv` and installs exactly what `uv.lock` pins —
   including **jvagent** from TestPyPI, which a bare `pip install` cannot reach
   without extra index flags (`pyproject.toml` carries a scoped
   `[tool.uv.index]` entry for it).

   ```bash
   cd backend
   uv sync --frozen --extra dev --extra test
   ```

   `dev` and `test` are **separate extras**. Plain `uv sync --frozen` prunes
   both; `--extra dev` alone drops `asgi_lifespan` and breaks the MCP tests.

   If `uv` reports that the pinned jvagent version does not exist, the index
   listing is cached — `uv lock --refresh-package jvagent`.

2. **Set up environment variables**

   From the repo root (not `backend/`):

   ```bash
   ./scripts/bootstrap_env.sh .env .env.example
   ```

   That copies the template if needed and replaces placeholder
   `JVSPATIAL_JWT_SECRET_KEY` / `INTEGRAL_CREDENTIAL_ENC_KEY` values. Boot
   refuses the example placeholders (even when they are ≥32 characters).

   **The template is not runnable as-is.** It ships those secrets as
   placeholders and sets no `DEBUG`, which defaults to `false`. Left alone,
   the API exits on a weak JWT secret; saving an API key returns
   `400 INTEGRAL_CREDENTIAL_ENC_KEY is not configured`; no model is
   reachable. To enable reload and fill the credential key by hand instead:

   ```bash
   printf 'DEBUG=true\nINTEGRAL_CREDENTIAL_ENC_KEY=%s\n' \
       "$(openssl rand -base64 32)" >> ../.env
   ```

   Then set a provider key. The default heavy model in `agent.yaml` is
   `ollama/glm-5.3:cloud`, which needs **both** of these — without
   `OLLAMA_API_BASE`, LiteLLM resolves the host as
   `OLLAMA_API_BASE or http://localhost:11434` and silently targets a *local*
   Ollama daemon:

   ```bash
   OLLAMA_API_BASE=https://ollama.com
   OLLAMA_API_KEY=<your key>
   ```

   Settings live in the **repo-root** `.env` on a checkout. Precedence is
   `shell environment > backend/.env > <repo-root>/.env > .env in the current
   working directory`. An exported shell variable beats the files, and a
   stale `backend/.env` shadows the root one (`backend/.env` is optional and
   normally absent). A pip-installed Core has no repo `.env`; start it from
   the distro directory so that `.env` loads. `integral init` writes it.
   The install and `integral web` commands are in the repository README
   under **Install a released Core**. To see where each setting actually
   resolved from:

   ```bash
   .venv/bin/python scripts/env_doctor.py
   ```

3. **Start Postgres, then the server**

   Postgres is the default (`JVSPATIAL_DB_TYPE=postgres` in `.env.example`).
   From the repo root:

   ```bash
   docker compose up -d db    # host :5433, db `integral`
   ```

   Then from `backend/`:

   ```bash
   .venv/bin/python -m app.main
   ```

   This uses jvspatial's recommended entrypoint: `server.run()` in `app/main.py` handles uvicorn, logging, and host/port from `HOST` and `PORT` in `.env`. Auto-reload is enabled when `DEBUG=True`.

The API will be available at `http://localhost:4000` (or `HOST:PORT` from `.env`)

## Breaking changes

See the repo-root [CHANGELOG.md](../CHANGELOG.md) for breaking API and graph changes. When upgrading dev/staging databases after major graph shape changes, delete or re-seed `JVSPATIAL_DB_PATH` data.

## jvspatial alignment

Integral hosts a [jvspatial](https://github.com/TrueSelph/jvspatial) `Server` and registers routes with `@endpoint`. Application errors follow jvspatial’s HTTP exception model (see [error-handling.md](https://github.com/TrueSelph/jvspatial/blob/main/docs/md/error-handling.md) and [api-architecture.md](https://github.com/TrueSelph/jvspatial/blob/main/docs/md/api-architecture.md)).

**Error JSON (typical):** `error_code`, `message`, optional `details`, plus handler metadata such as `timestamp` and `path`. Validation and a few auth paths may still return FastAPI’s `{"detail": ...}` shape (for example Pydantic `422` bodies).

**Configuration:** `app/config.py` uses [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) with the same environment variable names as before. On import it calls `jvspatial.env.load_env()` (after `python-dotenv`) so jvspatial’s cached env contract stays warm; tests clear that cache via `tests/conftest.py`.

**Tooling:** Repository root `.pre-commit-config.yaml` runs Black (88 columns), isort, flake8, and mypy against `backend/`. GitHub Actions workflow `.github/workflows/backend-ci.yaml` runs backend tests with coverage and pre-commit.

## Platform roadmap (post-MVP)

- **Real-time:** jvspatial change events → optional Redis or similar → WebSocket or SSE scoped to visible tracks; clients can replace pure polling with subscription-driven invalidation (see [docs/product/ARCHITECTURE.md](../docs/product/ARCHITECTURE.md) §6–7).
- **AI:** Separate deployable services using the same REST API with service credentials; user **opt-in** in preferences; start with rule-based helpers before LLM-backed features.

**Docker:** Root `docker-compose.yml` builds the API from `backend/Dockerfile` (jvspatial from PyPI per `pyproject.toml`). For production-like runs, set `DEBUG=False`, a strong `SECRET_KEY`, and a persistent `JVSPATIAL_DB_PATH` (or non-JsonDB backend) in `.env`.

## 📁 Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI application entry point
│   ├── config.py         # Configuration management
│   ├── api/              # API routes and endpoints
│   │   ├── auth.py, users.py, tracks.py, entries.py
│   │   ├── apps.py, workspaces.py, entry_types.py
│   │   ├── tags.py, comments.py, attachments.py, views.py, operational_models.py
│   │   ├── access.py, shares.py, shared_with_me.py, invitations.py
│   │   ├── feed.py, notifications.py, meta.py
│   │   └── __init__.py
│   ├── agentive/         # Always-on ops layer (harness + MCP + staging)
│   ├── models/           # Data models and schemas
│   │   ├── nodes.py, edges.py
│   │   └── __init__.py
│   └── services/         # Business logic layer
│       ├── permissions.py, sharing.py, share_links.py, invitations.py
│       ├── workspace_permissions.py, workspace_resolver.py, request_scope.py
│       ├── personal_workspace.py, uniqueness.py, edge_upsert.py
│       ├── operational_model_*.py, pagination.py
│       └── __init__.py
├── tests/                # Test suite
│   ├── conftest.py       # Fixtures (authenticated_client, test_user, etc.)
│   ├── test_crud_*.py    # CRUD tests for entities
│   ├── test_api.py
│   ├── test_permissions.py
│   ├── test_nodes.py
│   └── test_edges.py
├── pyproject.toml        # Project metadata and dependencies
├── uv.lock               # Resolved dependency lockfile (source of truth)
├── .gitignore           # Git ignore patterns
└── README.md            # This file
```

## 🔧 Configuration

Configuration is managed through environment variables. See `app/config.py` for all available options.

### Key Environment Variables

```bash
# Server
HOST=0.0.0.0
PORT=4000
DEBUG=True

# JWT Authentication
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# jvspatial
JVSPATIAL_DB_TYPE=json
JVSPATIAL_DB_PATH=integral_db

# Logging Configuration
LOG_LEVEL=INFO
JVSPATIAL_DB_LOGGING_ENABLED=true
JVSPATIAL_DB_LOGGING_LEVELS=ERROR,CRITICAL
JVSPATIAL_DB_LOGGING_DB_NAME=logs
JVSPATIAL_DB_LOGGING_API_ENABLED=true
JVSPATIAL_LOG_DB_TYPE=json
JVSPATIAL_LOG_DB_PATH=integral_logs

# Phase 7 — Pending Agent Write TTL (Plan 07-04)
# When a Policy has requires_human_approval=True and intercepts an agent
# write, a Approval is persisted with expires_at = now + TTL_DAYS.
# The reclaim loop wakes every RECLAIM_INTERVAL_HOURS and marks expired
# rows as status='expired' (emits approval.expired ChangeEvent — a
# system-emitted, audit-only action with no PolicyAction twin).
# Payloads exceeding PAYLOAD_MAX_BYTES return HTTP 413; the
# Approval is NOT persisted on overflow.
APPROVAL_TTL_DAYS=7
PENDING_WRITE_RECLAIM_INTERVAL_HOURS=1.0
APPROVAL_PAYLOAD_MAX_BYTES=1048576
```

## 🛠️ Development

### Running the Server

```bash
python -m app.main
```

With `DEBUG=True` (default), `server.run()` enables uvicorn auto-reload. Host and port come from `HOST` and `PORT` in `.env`. To override:

```bash
HOST=127.0.0.1 PORT=4001 python -m app.main
```

### Code Formatting

```bash
# Format code with black
black app/

# Sort imports with isort
isort app/

# Run both
black app/ && isort app/
```

### Type Checking

```bash
mypy app/
```

### Linting

```bash
flake8 app/
```

## 🧪 Testing

### Run Tests

```bash
# Run all tests (in-process)
pytest

```bash
# From repo root (serial — works with backend/tests/ path)
pytest backend/tests/ -v

# From backend/ (recommended — matches conftest cwd pin)
cd backend
pytest tests/ -v

# Fast parallel (~6 min default lane) — run from backend/
pytest -q -n auto --dist loadfile

# Pure unit slice (~11s)
pytest -q -m unit

# Full CI parity (slow gates)
INTEGRAL_RUN_SLOW_TESTS=1 pytest -q

# Override default marker filter (include domain_app + slow)
pytest -q -m '' -n auto

# Strip all addopts (e.g. verbose from repo root without -q)
pytest backend/tests/ -v -o addopts=''
```

# Tests that need disk library packages are auto-marked ``library``; opt in explicitly:
pytest -q -m library

# Run with coverage
pytest --cov=app

# Run CRUD tests only
pytest tests/test_crud_*.py -v

# Run specific entity tests
pytest tests/test_crud_entries.py -v
pytest tests/test_crud_views.py -v

# Run against live server (recommended for full auth coverage)
# Terminal 1: start the server
python -m app.main

# Terminal 2: run tests (HOST/PORT from .env, or set BASE_URL)
BASE_URL=http://localhost:4000 pytest -v

# Run with verbose output
pytest -v
```

**Note:** In-process tests use httpx ASGITransport; JWT auth validation can fail due to ASGI scope handling. For full test coverage, run the server and use `BASE_URL=http://localhost:4000`.

### Test Suite Structure

The backend has comprehensive CRUD test coverage for all major entities and views:

| Test File | Entity | Coverage |
|-----------|--------|----------|
| `test_crud_users.py` | Users | Create (via auth), List, Get, Update, Delete, Profile |
| `test_crud_workspaces.py` (planned; current coverage lives in `test_app_graph.py`, `test_invitations.py`, `test_workspace_scope_enforcement.py`) | Workspaces | App graph shell + per-workspace branch registries + scope enforcement |
| `test_crud_spaces.py` | Apps | Full CRUD, Tracks add/remove, Collaborators add/remove |
| `test_crud_tracks.py` | Tracks | Full CRUD, Entries list, Collaborators add/remove |
| `test_crud_entries.py` | Entries | Full CRUD, Tags add/remove, Reactions add/remove/toggle |
| `test_crud_entry_types.py` | Entry Types | Full CRUD (track-scoped) |
| `test_crud_tags.py` | Tags | Full CRUD (track-scoped) |
| `test_crud_comments.py` | Comments | Create, List, Update, Delete, Threaded replies |
| `test_crud_views.py` | Views | Full CRUD (saved track view configs) |
| `test_crud_attachments.py` | Attachments | Get, Delete; Upload skipped (multipart/JSON limitation) |
| `test_crud_feed.py` | Feed | Get feed, pagination, track filter, create/update/delete via feed_entries |
| `test_crud_notifications.py` | Notifications | List, Create, Mark read, Mark all read, Delete |

Additional test modules: `test_api.py`, `test_permissions.py`, `test_nodes.py`, `test_edges.py`.

### Writing Tests

Tests are located in the `tests/` directory. Use the `authenticated_client` fixture for authenticated requests:

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_track(authenticated_client: AsyncClient, test_user):
    response = await authenticated_client.post(
        "/api/tracks",
        json={"title": "My Track", "visibility": "private"},
    )
    assert response.status_code == 200
    assert "track" in response.json()
```

## 📚 API Documentation

Once the server is running, interactive API documentation is available at:

- **Swagger UI**: http://localhost:4000/docs
- **ReDoc**: http://localhost:4000/redoc
- **OpenAPI JSON**: http://localhost:4000/openapi.json

## 🔌 API Endpoints

### Health Check

```bash
GET /health
```

Returns the health status of the API.

### Entity Endpoints

The API provides full CRUD for all major entities. Key resource groups:

| Resource | Endpoints |
|----------|-----------|
| **Auth** | `/api/auth/signup`, `/api/auth/login`, `/api/auth/me`, `/api/auth/update-profile` |
| **Users** | `/api/users` (GET, POST), `/api/users/{id}` (GET, PUT, DELETE) |
| **Workspaces** | `/api/workspaces`, `/api/workspaces/{id}`, `/api/workspaces/{id}/members`, `/api/workspaces/{id}/invitations`, `/api/workspaces/{id}/apps`, `/api/workspaces/{id}/tracks`, `/api/workspaces/{id}/storage-usage` |
| **Invitations** | `/api/invitations/{token}` (preview), `/api/invitations/{token}/accept`, `/api/invitations/{token}/decline`; resource-level: `POST /api/{apps\|tracks\|entries}/{id}/invitations` |
| **Apps** | `/api/apps`, `/api/apps/{id}/tracks`, `/api/apps/{id}/collaborators`, `/api/apps/{id}/exclusions`, `/api/apps/{id}/access` |
| **Tracks** | `/api/tracks`, `/api/tracks/{id}/entries`, `/api/tracks/{id}/collaborators`, `/api/tracks/{id}/exclusions`, `/api/tracks/{id}/access` |
| **Entries** | `/api/entries`, `/api/entries/{id}/tags`, `/api/entries/{id}/reactions`, `/api/entries/{id}/comments`, `/api/entries/{id}/collaborators`, `/api/entries/{id}/exclusions`, `/api/entries/{id}/access` |
| **Access (unified)** | `/api/{apps\|tracks\|entries}/{id}/collaborators`, `/api/{apps\|tracks\|entries}/{id}/exclusions`, `/api/{apps\|tracks\|entries}/{id}/access` |
| **Shares** | `/api/{apps\|tracks\|entries}/{id}/shares` (mint, list), `/api/shares/{share_link_id}` (DELETE = revoke), `/api/shares/redeem` (POST) |
| **Shared with me** | `/api/me/shared` (aggregated cross-workspace resources), `/api/me/invitations` (pending invites) |
| **Entry Types** | `/api/entry-types` |
| **Tags** | `/api/tags` |
| **Comments** | `/api/entries/{id}/comments`, `/api/comments/{id}` |
| **Views** | `/api/tracks/{id}/views`, `/api/views/{id}` |
| **Operational Models** | `/api/operational-models`, `/api/apps/{id}/operational-model`, `/api/tracks/{id}/operational-model`, `/api/operational-models/{id}/{draft,publish,diff,discard-draft}`, `/api/operational-model-substrate` |
| **Attachments** | `/api/entries/{id}/attachments`, `/api/attachments/{id}` |
| **Feed** | `/api/feed`, `/api/feed_entries` |
| **Notifications** | `/api/notifications` |

All list endpoints require the `X-Integral-Scope: <workspace_id>` request header (backend-authoritative workspace isolation — see `services/request_scope.py`).

## 🔐 Authentication

Authentication is provided by jvspatial. When `auth_enabled=True`, the server registers `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, and Integral adds `/api/auth/signup` and `/api/auth/me`.

- **Request state**: After successful auth, jvspatial sets `request.state.user`. Protected endpoints receive `user_id` via the `@endpoint` decorator.
- **Test mode**: When `TESTING=1`, `TestAuthBypassMiddleware` pre-sets `request.state.user` from the JWT so in-process ASGI tests work without hitting the real auth flow.
- **Exempt paths**: Login, register, refresh, and signup are exempt; all other `/api/*` routes require authentication unless explicitly registered with `auth=False`.

## 🗄️ Database

The backend uses jvspatial for spatial database operations. By default, it uses JSON file-based storage for development.

### jvspatial Integration

jvspatial provides:
- Spatial database operations
- Graph-based data relationships
- Async/await support
- Multiple database backends (JSON, SQLite, MongoDB)

Example usage:

```python
from jvspatial.core.root import Root
from jvspatial.db.base import DatabaseInterface

# Initialize database
db = DatabaseInterface.create("json", base_path="integral_db")
root = Root(db=db)

# Use in your endpoints
```

## 📊 Logging

Integral uses jvspatial's DB Logging subsystem for comprehensive logging capabilities:

### Features

- **Automatic Database Logging**: Errors and critical events are automatically persisted to a separate logging database
- **Structured Logging**: Console output with colored log levels and structured formatting
- **Queryable Logs**: Logs can be queried via REST API endpoints
- **Configurable Levels**: Choose which log levels are persisted to the database
- **Multiple Backends**: Logging database supports JSON, SQLite, MongoDB, and DynamoDB

### Configuration

Logging is configured via environment variables:

```bash
# Console logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL=INFO

# Enable/disable database logging
JVSPATIAL_DB_LOGGING_ENABLED=true

# Comma-separated list of log levels to persist (default: ERROR,CRITICAL)
JVSPATIAL_DB_LOGGING_LEVELS=ERROR,CRITICAL

# Name of the logging database
JVSPATIAL_DB_LOGGING_DB_NAME=logs

# Enable/disable logging API endpoints
JVSPATIAL_DB_LOGGING_API_ENABLED=true

# Logging database type (defaults to same as main DB)
JVSPATIAL_LOG_DB_TYPE=json

# Logging database path (for file-based databases)
JVSPATIAL_LOG_DB_PATH=integral_logs
```

### Using Logging in Code

The logging system automatically captures standard Python logging calls:

```python
import logging

logger = logging.getLogger(__name__)

# Standard logging - automatically captured if level matches
logger.error("Database connection failed", extra={
    "status_code": 500,
    "event_code": "database_error",
    "path": "/api/users",
    "method": "POST",
    "details": {"database": "users", "host": "localhost"}
})

# With exception traceback
try:
    risky_operation()
except Exception:
    logger.error("Operation failed", exc_info=True, extra={
        "event_code": "operation_failed",
        "status_code": 500
    })
```

### Querying Logs via API

When `JVSPATIAL_DB_LOGGING_API_ENABLED=true`, logs can be queried via REST API:

```bash
# Get all logs (paginated)
GET /api/logs?page=1&page_size=50

# Filter by log level
GET /api/logs?category=ERROR

# Filter by date range
GET /api/logs?start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z

# Filter by agent_id (if used)
GET /api/logs?agent_id=agent_123
```

### Log Database Location

- **JSON**: `integral.logs/` directory (default)
- **SQLite**: `integral_logs/sqlite/integral.logs`
- **MongoDB**: Uses the configured MongoDB URI and database name

Logs are stored separately from application data, allowing for independent backup, archival, and analysis.

## 🔐 Authentication

The backend is configured for JWT-based authentication:

1. User logs in with credentials
2. Server returns JWT token
3. Client includes token in Authorization header
4. Server validates token for protected endpoints

Example:

```bash
# Login
POST /api/auth/login
{
  "username": "user@example.com",
  "password": "password"
}

# Use token
GET /api/tracks
Authorization: Bearer <token>
```

## 📦 Dependencies

### Core Dependencies

- **FastAPI**: Modern, fast web framework
- **uvicorn**: ASGI server
- **pydantic**: Data validation
- **jvspatial**: Spatial database framework
- **python-jose**: JWT token handling
- **passlib**: Password hashing

### Development Dependencies

- **pytest**: Testing framework
- **black**: Code formatter
- **isort**: Import sorter
- **mypy**: Type checker
- **flake8**: Linter

## 🚢 Deployment

### Production Considerations

1. **Change SECRET_KEY**: Use a strong, random secret key
2. **Disable DEBUG**: Set `DEBUG=False` in production
3. **Use production database**: Switch from JSON to MongoDB or PostgreSQL
4. **Set up HTTPS**: Use reverse proxy (nginx, Caddy)
5. **Configure CORS**: Restrict allowed origins
6. **Configure logging**:
   - Set `LOG_LEVEL=INFO` or `WARNING` in production
   - Ensure `JVSPATIAL_DB_LOGGING_ENABLED=true` for error tracking
   - Consider using MongoDB or DynamoDB for logging database in production
   - Set up log rotation/archival policies
7. **Use environment variables**: Never hardcode secrets

### Running in Production

```bash
# Install production dependencies only (no dev/test extras)
uv sync --frozen

# Option 1: jvspatial entrypoint (single worker; set DEBUG=False)
python -m app.main

# Option 2: gunicorn with uvicorn workers (multi-worker)
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:4000

# Option 3: uvicorn directly (multi-worker)
uvicorn app.main:app --host 0.0.0.0 --port 4000 --workers 4
```

For production, prefer gunicorn or uvicorn with workers. The `python -m app.main` entrypoint is ideal for development and single-worker deployments.

## 🐳 Docker (Optional)

Create a `Dockerfile` in the backend directory:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY . .

# jvspatial entrypoint (server.run() in app.main)
CMD ["python", "-m", "app.main"]
```

Build and run:

```bash
docker build -t integral-backend .
docker run -p 4000:4000 integral-backend
```

## 🤝 Contributing

1. Follow PEP 8 style guide
2. Use type hints
3. Write tests for new features
4. Run linting and formatting before committing
5. Update documentation

## 📝 Code Style

- **Line length**: 100 characters (black default)
- **Import sorting**: isort with black profile
- **Type hints**: Use throughout the codebase
- **Docstrings**: Google style docstrings

## 🔍 Troubleshooting

### Common Issues

**Import errors**
```bash
# Reinstall exactly what the lockfile pins
uv sync --frozen --extra dev --extra test
```

Prefer calling `.venv/bin/python` directly over `source venv/bin/activate`. A
venv copied or moved between machines keeps the absolute path of the machine
that built it, so `activate` silently prepends a directory that no longer
exists and `python` drops off PATH entirely. If that has happened, delete
`backend/.venv` and re-run the sync above.

**Port already in use**
```bash
# Change PORT in .env, or override:
PORT=4001 python -m app.main
```

**Database errors**
```bash
# Check database file permissions
# Verify JVSPATIAL_DB_PATH in .env
# Delete and recreate database if needed
```

## 📞 Support

For issues and questions:
- Check the [main README](../README.md)
- Technical docs hub: [docs/README.md](../docs/README.md)
- Review API documentation at `/docs`
- Open an issue on the repository

## 📄 License

MIT License - see LICENSE file for details

