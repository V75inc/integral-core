# Technology Stack

## Backend

### Core Framework
- **Python**: 3.9+ (3.14 supported)
- **FastAPI**: REST API via jvspatial `@endpoint` decorator
- **jvspatial**: Object-spatial graph framework (GitHub dev branch)
- **uvicorn**: ASGI server with auto-reload in dev

### Database
- **Default**: PostgreSQL with asyncpg driver (recommended production)
- **Alternatives**: SQLite (file-backed dev), JSON (legacy), MongoDB
- **Vector Store**: MongoDB Atlas Vector Search (for semantic retrieval)

### Key Dependencies
- **pydantic**: Data validation and settings (v2+)
- **python-jose**: JWT authentication
- **passlib**: Password hashing (bcrypt)
- **authlib**: OAuth 2.1 Authorization Server
- **mcp**: Model Context Protocol (v1.27+)
- **jvagent**: Agent runtime (GitHub dev-executive branch)
- **fastembed**: ONNX-based embeddings (no torch dependency)
- **pynacl**: Ed25519 bundle verification
- **sentry-sdk**: Production error reporting (optional)

### Attachment Processing (soft-imported)
- **python-magic**: MIME detection (requires libmagic)
- **pdfplumber/pypdf**: PDF extraction
- **python-docx/openpyxl/python-pptx**: Office formats
- **Pillow**: Image metadata
- **mutagen**: Audio metadata
- **ffprobe** (system): Video metadata

## Frontend

### Core Framework
- **React**: 18.2
- **TypeScript**: Strict mode
- **Vite**: Build tool (port 9006)
- **React Router**: v6 with v7 future flags

### Styling & UI
- **Tailwind CSS**: Utility-first styling
- **PostCSS**: CSS processing
- **lucide-react**: Icon library
- **class-variance-authority**: Component variants

### State & Data
- **TanStack Query**: Server state management
- **axios**: HTTP client with workspace scope injection
- **Context API**: Auth, scope, toast, system notifications

### Specialized Libraries
- **@assistant-ui/react**: AI chat interface
- **@dnd-kit**: Drag and drop
- **@tiptap**: Rich text editor (markdown support)
- **mammoth**: DOCX rendering
- **pdfjs-dist**: PDF preview
- **xlsx**: Spreadsheet parsing
- **date-fns**: Date formatting

### Testing
- **Vitest**: Test runner
- **React Testing Library**: Component testing
- **jsdom**: DOM simulation

## Common Commands

### Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
uv sync --frozen --extra dev --extra test   # dev + test are SEPARATE extras
```

### Backend Development
```bash
python -m app.main                    # Start server (port 4000)
pytest                                # Run tests
pytest --cov=app                      # With coverage
black app/ && isort app/              # Format code
mypy app/                             # Type check
flake8 app/                           # Lint
```

### Frontend Setup
```bash
cd frontend
npm install
```

### Frontend Development
```bash
npm run dev           # Dev server (port 9006, proxies /api to :4000)
npm run build         # Production build (tsc + vite build)
npm run preview       # Preview built artifacts
npm run test          # Vitest watch mode
npm run test:run      # Single test pass (CI)
npm run lint:types    # TypeScript type check
```

### Git Hooks (one-time setup)
```bash
git config core.hooksPath .githooks
```

Enforces:
- jvspatial drift check (blocks raw FastAPI patterns)
- Graph contiguousness check (blocks Node.create without edge wire)

### Environment Setup
```bash
cp .env.example .env
# Edit .env with your configuration
# Minimum required: JVSPATIAL_JWT_SECRET_KEY
```

### Database (Docker)
```bash
docker compose up -d db    # Start PostgreSQL (port 5433)
```

## Build System

### Backend
- **setuptools**: Build backend
- **pyproject.toml**: Project metadata and dependencies
- **uv.lock**: Resolved dependency lockfile — the source of truth for what installs

### Frontend
- **Vite**: Fast HMR, optimized production builds
- **TypeScript**: ES2020 target
- **package.json**: npm scripts and dependencies

## Code Quality Tools

### Backend
- **black**: Code formatter (88 char line length)
- **isort**: Import sorting (black profile)
- **mypy**: Static type checking
- **flake8**: Linting with plugins
- **pytest**: Testing framework with async support
- **pytest-cov**: Coverage reporting (80% threshold)

### Frontend
- **ESLint**: Code linting
- **Prettier**: Code formatting (via Tailwind plugin)
- **TypeScript compiler**: Type checking

## Architecture Patterns

### jvspatial Object-Spatial Contract
1. **Data on nodes**: Entities are Pydantic `Node` subclasses
2. **Semantics on edges**: Relationship state on typed edge fields
3. **Behavior via walkers**: Multi-hop computation uses `Walker` pattern
4. **Branch nodes**: Collections/registries are dedicated nodes

### Forbidden Patterns
- ❌ `from fastapi import APIRouter` → Use `@endpoint`
- ❌ `raise HTTPException` → Use `JVSpatialAPIException` subclasses
- ❌ Inline Pydantic models in `api/*.py` → Move to `schemas/`
- ❌ `Node.create()` without edge wire → Violates I-GRAPH-01
- ❌ `edge.context.get("role")` → Use typed edge fields

### Required Patterns
- ✅ `@endpoint` decorator for all routes
- ✅ Pydantic models in `backend/app/schemas/`
- ✅ Wire structural edge at `Node.create()` time
- ✅ Use `policy_engine.evaluate()` for authorization
- ✅ Emit change events via `emit_change_event()`

## Development Environment

- **Node.js**: 18+
- **Python**: 3.9-3.14
- **Git**: Version control with custom hooks
- **PostgreSQL**: 16+ with pgvector (recommended)
- **System libraries**: libmagic, ffmpeg (optional for attachments)

## API Documentation

- **Swagger UI**: http://localhost:4000/docs
- **ReDoc**: http://localhost:4000/redoc
- **OpenAPI JSON**: http://localhost:4000/openapi.json
