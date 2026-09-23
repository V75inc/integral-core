# Scope: Connector Subsystem Modernization for integral-core

Porting the advanced connector features from integral into integral-core, bringing multi tenant workspace connection scoping, native Google Workspace drivers, resident agent tool awareness, and mutation staging governance to the core substrate.

**Build approach:** Tracer Bullet (vertical slices; each feature built end to end through every layer, working).
**Workflow:** Beta (after develop, check verify then test. High confidence for production substrate).

These are recommendations to keep your build orderly, not requirements. Skip anything that does not fit. If you already know how to build a feature, use /develop and skip /architect. You decide when a feature is done.

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| 1 | Baseline connector substrate | Foundation | existing |
| 2 | Connection scoping and row resolution | Slice 1 | planned |
| 3 | Shared row authorization and policies | Slice 1 | planned |
| 4 | Google OAuth and token management | Slice 2 | planned |
| 5 | Native Google Drive connector | Slice 2 | planned |
| 6 | Native Google Sheets connector | Slice 2 | planned |
| 7 | Native Gmail live tools | Slice 2 | planned |
| 8 | Resident agent connected data sources | Slice 3 | planned |
| 9 | Native tool mutation staging | Slice 3 | planned |
| 10 | Settings UI scoping and custom MCP mount | Slice 4 | planned |
| 11 | Unified tools inspector | Slice 4 | planned |

## Foundations

### 1. Baseline connector substrate · existing
Existing connector foundation in integral-core providing base SyncConnector, MCP client mount, catalog loader, and credential at rest encryption.
**Done when:** core connector models, sync loop, and keyless friendly encryption tests pass.

## Slice 1: Connection scoping and row resolution

### 2. Connection scoping and row resolution
Add connection mode and operator label to Connector nodes, with row resolution prioritizing personal rows over shared rows and returning actionable errors when unconfigured.
**Done when:** Connector node stores connection mode and label, row resolution picks personal then shared rows, and test suite proves precedence.
- [ ] Build it: `/develop connection scoping and row resolution`

### 3. Shared row authorization and policies
Policy engine integration allowing workspace members to invoke tools and read status on shared rows, while restricting updates and deletions to workspace admins.
**Done when:** workspace members can invoke shared connector tools, non members are denied, and admins manage shared rows.
- [ ] Build it: `/develop shared row authorization and policies`

## Slice 2: Native Google Workspace connectors

### 4. Google OAuth and token management
Shared Google OAuth helper with signed state tokens, expiration checks, provider binding, and automatic token refresh without leaking secrets to logs.
**Done when:** consent URL builds with signed state, token exchange persists encrypted tokens, and refresh rotates access tokens automatically.
- [ ] Build it: `/develop Google OAuth and token management`

### 5. Native Google Drive connector
Direct Drive v3 REST API driver providing twelve live search, read, write, and permission tools plus entry sync mirror, deprecating hosted MCP mounts.
**Done when:** all twelve Drive tools execute against Google REST APIs, write actions declare write flags, and catalog offers drive_native.
- [ ] Build it: `/develop native Google Drive connector`

### 6. Native Google Sheets connector
Direct Sheets v4 REST API driver providing nine spreadsheet inspection, value reading, cell editing, and batch update tools with row and column caps.
**Done when:** reading and updating spreadsheet cells succeeds with fresh tokens and payload limits are enforced.
- [ ] Build it: `/develop native Google Sheets connector`

### 7. Native Gmail live tools
Upgrade Gmail connector from sync only to include thirteen live tools for searching threads, drafting messages, sending emails, and managing labels.
**Done when:** live email tools execute against Gmail REST endpoints and write actions stage for blessing.
- [ ] Build it: `/develop native Gmail live tools`

## Slice 3: Agent awareness and mutation staging

### 8. Resident agent connected data sources
Inject prompt overlay documentation informing the resident agent about all ready tools in the workspace and noting whether each is personal, shared, or unavailable.
**Done when:** agent prompt includes connected data sources doc with canonical tool keys and accurate readiness states.
- [ ] Build it: `/develop resident agent connected data sources`

### 9. Native tool mutation staging
Register native_tool_call staged cards for write classified native tools so the resident agent proposes changes for user approval before mutating external data.
**Done when:** creating files, editing cells, or sending emails creates a pending approval card that executes upon blessing.
- [ ] Build it: `/develop native tool mutation staging`

## Slice 4: Settings UI and inspector polish

### 10. Settings UI scoping and custom MCP mount
Frontend settings interface supporting scope filter chips, origin tags, connection mode selection cards, platform credential hiding, and custom MCP modal.
**Done when:** user can filter connections by scope, install shared or personal connections, and mount custom MCP servers.
- [ ] Build it: `/develop Settings UI scoping and custom MCP mount`

### 11. Unified tools inspector
Extend the tools inspector modal to inspect first party native tools alongside external MCP tools, displaying parameter schemas and approval tags.
**Done when:** clicking inspect tools on native or MCP rows opens the modal showing all callable tools and search filtering works.
- [ ] Build it: `/develop unified tools inspector`

## Legend

**The decision box.** Every feature carries its entry command.
**Feature lifecycle**: the scope updates as features move from planned to in-progress to done.
