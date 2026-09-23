# Project Structure

## Monorepo Layout

```
integral/
├── backend/              Python FastAPI + jvspatial (port 4000)
├── frontend/             React + TypeScript + Vite (port 9006)
├── agent/                jvagent bundle (integral_agent)
├── docs/                 Technical documentation hub
├── .githooks/            Pre-commit substrate guards
├── .ci/                  CI drift check scripts
└── .kiro/                Kiro-specific configuration
```

## Backend Structure (`backend/`)

### Core Directories

```
backend/
├── app/
│   ├── api/              REST endpoints via @endpoint
│   ├── agentive/         Always-on ops layer (harness + MCP + staging)
│   ├── models/           Node and Edge definitions
│   ├── schemas/          Pydantic request/response models
│   ├── services/         Business logic layer
│   ├── middleware/       Request processing
│   ├── views/            View type contracts
│   ├── plugins/          Signed code plugins
│   └── main.py           Application entry point
├── tests/                Test suite
├── uv.lock               Resolved dependency lockfile (source of truth)
└── pyproject.toml        Project metadata
```

### API Endpoints (`backend/app/api/`)

All routes use jvspatial `@endpoint` decorator:

- **auth.py**: Authentication (signup, login, refresh, verify-email)
- **users.py**: User CRUD
- **workspaces.py**: Workspace management, members, invitations
- **apps.py**: App CRUD, tracks, collaborators
- **tracks.py**: Track CRUD, entries, collaborators
- **entries.py**: Entry CRUD, tags, reactions, comments
- **entry_types.py**: EntryType definitions
- **tags.py**: Tag management
- **comments.py**: Comment threads
- **views.py**: Saved view configurations
- **operational_models.py**: OperationalModel CRUD, draft/publish
- **attachments.py**: File uploads
- **access.py**: Unified collaborators/exclusions/access snapshots
- **shares.py**: Share link mint/redeem/revoke
- **shared_with_me.py**: Aggregated cross-workspace resources
- **invitations.py**: Invitation preview/accept/decline
- **feed.py**: Activity feed
- **notifications.py**: User notifications
- **retrieve.py**: Semantic/hybrid retrieval
- **audit_log.py**: Change event history
- **meta.py**: System metadata

### Models (`backend/app/models/`)

**nodes.py**: Node definitions (all inherit from `jvspatial.core.Node`)
- User, Workspace, App, Track, Entry
- EntryType, Tag, View, OperationalModel
- Comment, Attachment, Notification
- Invitation, ShareLink, Policy, Connector
- Registry nodes: Users, Workspaces, Apps, Tracks, etc.

**edges.py**: Edge definitions (PascalCase class + ALL_CAPS alias)
- OWNS, IS_MEMBER_OF, COLLABORATES_ON, EXCLUDED_FROM
- CONTAINS, HAS_OPERATIONAL_MODEL, DEFINES_TRACK_PROFILE
- IS_OF_TYPE, TAGGED_WITH, REFERENCES
- HAS_COMMENT, AUTHORED_BY, MENTIONS
- HAS_ATTACHMENT, HAS_NOTIFICATION
- INVITED_TO, CATALOGS, HAS_POLICY
- ANCHORS, TEMPLATED_FROM

### Services (`backend/app/services/`)

Business logic layer:
- **permissions.py**: Role resolution (`resolve_role`)
- **sharing.py**: Collaborator/exclusion management
- **share_links.py**: Share link operations
- **invitations.py**: Invitation lifecycle
- **workspace_permissions.py**: Workspace access checks
- **workspace_resolver.py**: Workspace scope resolution
- **request_scope.py**: X-Integral-Scope header validation
- **personal_workspace.py**: Auto-provision personal workspace
- **operational_model_*.py**: OperationalModel operations
- **change_event_logger.py**: Change event emission
- **policy_engine.py**: Authorization decisions
- **embedding_store.py**: Vector store abstraction
- **agent_scratch.py**: Per-user scratch tracks

### Agentive Layer (`backend/app/agentive/`)

Always-on in `main.py` (not gated by `AGENTIVE_ENABLED` — that env flag is not a live boot control):
- **api/**: Agent-specific endpoints
- **connectors/**: External system integrations
- **mcp/**: MCP tool implementations
- **tooling/**: tool_manifest catalogue / dispatch / policy gate
- **services/**: Agent services
- **facet.py**: effective_facet helpers (ADR-003 dual-write)

## Frontend Structure (`frontend/src/`)

### Core Directories

```
frontend/src/
├── api/              axios client + per-resource modules
├── components/       React components
├── pages/            Route-level pages
├── features/         Feature modules (ai-chat, settings)
├── hooks/            Custom React hooks
├── context/          React context providers
├── utils/            Utility functions
├── lib/              Operational Model, telemetry
├── views/            View registry and manifests
├── App.tsx           Route table
└── main.tsx          Application entry point
```

### Components (`frontend/src/components/`)

Organized by domain:

**ui/**: Base primitives
- Button, Modal, Toast, Input, Select, etc.
- FieldRenderer (type-aware field display)

**entries/**: Entry composition
- EntryComposer, EntryDetail, EntryCard
- fieldTypes/: Field type registry and renderers
  - text, number, boolean, date, datetime
  - markdown, json, select, multi_select
  - relation, computed, file, files

**tracks/**: Track management
- TrackModal, TrackHeader, PinButton
- TrackCard, TrackList

**apps/**: App management
- AppModal, AppHeader, PinButton
- AppCard, AppList

**views/**: View palette
- registry.tsx: Central view type registry
- manifests/: Auto-discovered view definitions
- composable/: Meta-widgets (list, grid, board, timeline)
- plugins/auto.ts: Runtime plugin loader

**layout/**: Application chrome
- Sidebar, WorkspaceSwitcher
- EmailVerificationBanner

**collab/**: Collaboration UI
- UserSearchPicker, ShareDialog
- CollaboratorList, InvitationCard

**system/**: System notifications
- SystemNotificationBar (priority queue)
- SystemNotificationsProvider
- apiErrorNotifier, useOfflineNotification

**sidebar/**: Navigation rail
- Nav items, pinned resources

**chat/**: AI chat interface
- ChatComposer, MessageRenderer
- Thread management

**feed/**: Activity feed
- FeedCard, FeedFilterStrip
- Retrieval search UI

**notifications/**: Notifications
- NotificationCard (structured Actor: rest)

**settings/**: Settings sections
- Profile, Policies, Search, Notifications

**command/**: Command palette (⌘K)
- Workspaces, Pinned, Apps, Tracks, Entries, Actions

### Pages (`frontend/src/pages/`)

Route-level components:
- **MissionControlPage**: Dashboard
- **AppsPage**: App listing
- **AppDetailPage**: App detail and tracks
- **TracksPage**: Track listing
- **TrackDetailPage**: Track entries and views
- **FeedPage**: Activity feed
- **SettingsPage**: User settings
- **AIChatPage**: AI assistant
- **OperationalModelsPage**: Library browser
- **WorkspacesPage**: Workspace management
- **SharedWithMePage**: Shared resources (retired route, redirects to /)
- **LoginPage, SignupPage**: Authentication
- **ForgotPasswordPage, ResetPasswordPage**: Password recovery
- **VerifyEmailPage**: Email verification
- **InvitationAcceptPage**: Invitation redemption

### API Client (`frontend/src/api/`)

Axios-based with automatic workspace scope injection:

- **client.ts**: Base axios instance with interceptors
- **auth.ts**: Authentication endpoints
- **workspaces.ts**: Workspace operations
- **apps.ts**: App CRUD
- **tracks.ts**: Track CRUD
- **entries.ts**: Entry CRUD
- **sharing.ts**: Collaborators, share links, invitations
- **operationalModels.ts**: OperationalModel operations
- **retrieval.ts**: Semantic search
- **feed.ts, notifications.ts**: Activity surfaces

## Documentation (`docs/`)

Technical reference hub:

```
docs/
├── product/          Product vision, PRD, architecture, roadmap
│   ├── CONCEPT.md        Product vision
│   ├── PRD.md            Requirements
│   ├── ARCHITECTURE.md   System design
│   ├── ROADMAP.md        Milestone sequencing
│   └── BYOA.md           Bring-Your-Own-Agent
├── operational-models/ OperationalModel substrate docs
│   ├── README.md         Overview and tenets
│   ├── VIEW_PALETTE.md   View type contracts
│   └── COMPOSITION_PATTERNS.md  Modeling patterns
├── backend/          Backend-specific docs
├── ops/              Deployment guides
├── INVARIANTS.md     Substrate invariants (I-GRAPH-01, etc.)
└── README.md         Documentation index
```

## Configuration Files

### Root Level
- **.env**: Environment variables (copy from .env.example)
- **.gitignore**: Git exclusions
- **docker-compose.yml**: PostgreSQL + services
- **AGENTS.md**: Repository conventions for AI assistants
- **CHANGELOG.md**: Breaking changes
- **CONTRIBUTING.md**: Development workflow

### Backend
- **pyproject.toml**: Project metadata, dependencies, tool configs
- **uv.lock**: Resolved dependency lockfile — the source of truth for what installs
- **.flake8**: Flake8 configuration
- **.gitignore**: Backend-specific exclusions

### Frontend
- **package.json**: npm scripts and dependencies
- **vite.config.ts**: Vite configuration (API proxy)
- **vitest.config.ts**: Test configuration
- **tsconfig.json**: TypeScript configuration
- **tailwind.config.js**: Tailwind CSS configuration
- **postcss.config.js**: PostCSS configuration

## Key Conventions

### Backend Naming
- **Routes**: `@endpoint` in `api/*.py`
- **Nodes**: PascalCase classes in `models/nodes.py`
- **Edges**: PascalCase class + ALL_CAPS alias in `models/edges.py`
- **Schemas**: Pydantic models in `schemas/*.py`
- **Services**: Business logic functions in `services/*.py`

### Frontend Naming
- **Components**: PascalCase (e.g., `EntryComposer.tsx`)
- **Hooks**: camelCase with `use` prefix (e.g., `useScope.ts`)
- **Utils**: camelCase (e.g., `humanizeFieldKey.ts`)
- **Pages**: PascalCase with `Page` suffix (e.g., `TrackDetailPage.tsx`)

### Graph Structure
All entities follow the contiguousness invariant (I-GRAPH-01):
- Every Node MUST be reachable from Root via named edges
- Wire structural edge at create time in same transaction
- No detached nodes allowed

### Access Model
Three-layer authorization:
1. **Workspace gate**: Backend validates X-Integral-Scope header
2. **Resource cascade**: App → Track → Entry (strongest role wins)
3. **Explicit deny**: EXCLUDED_FROM overrides inherited paths only
