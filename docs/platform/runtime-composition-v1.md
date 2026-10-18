# Runtime composition contract v1

**Accepted direction: 18 October 2026. Implemented and qualified locally; review/publication pending.**

Core has one graph, editor, resident harness, App contract and authorization model.
Trusted process startup selects `INTEGRAL_RUNTIME_PRESET=local|hosted`. Existing
`desktop_single_user` / `server` installation profiles remain compatible and are
not rewritten. A running store cannot be switched into a different deployment.

| Contract | Local | Hosted |
| --- | --- | --- |
| Human admission | Installation-bound owner | Authenticated accounts |
| Workspace kinds | Personal | Personal and organization, subject to access |
| Identity admission, collaboration, public sharing | Disabled | Supported; normal authorization still required |
| Identity adapter | Atomic fixed-owner bootstrap and controlled session exchange | Existing verified account authentication and admin bootstrap |
| Transport adapter | Exact loopback/origin perimeter | Existing hosted authentication, CORS and perimeter |
| Records, Apps, agents, schema and recovery | Shared Core implementation | Shared Core implementation |

`app.runtime.composition` defines immutable descriptors. Resource services call
`require_capability` at their command boundaries. `app.runtime.operations` records
an explicit operation group for every PolicyAction and known legacy action.
Unknown operations fail closed before human, agent, connector or system grants.
App-defined operations retain the installed-App/resource-bound policy contract;
classification never grants access. A coverage test requires new PolicyAction
members to receive explicit metadata.

Identity admission is distinct from collaboration. Core owns secure principal
creation, sessions and graph transactions. Neutral identity and transport adapters
encapsulate the differences; products supply their account integration and native
controller bridge. The built-in local adapter contains no Clerk, Electron or One
logic. Hosted capability availability does not bypass workspace membership,
resource permissions, exclusions, approval or tenant separation.

One owns Electron, its native main/preload bridge, product signup/Clerk receipts,
branding, installers and signing. Business owns its account/commercial integration.
Neither product-account registration nor a receipt grants Core resource authority.
A cloud account outage cannot invalidate local owner sessions or stored data.

Compatibility facades preserve v1 imports and response fields. Keep existing owner
and workspace IDs, the DesktopIdentity persistence discriminator/marker, app ID,
data paths, installation keys and receipt verification keys. No identity migration,
new signup requirement or database replacement is part of this refactor.

The managed launcher derives its preset from the stored profile and overrides
ambient process/settings values. Settings select trusted composition, not a user
request, App preference or client header. Do not add arbitrary feature-flag
combinations or dynamic adapter/plugin discovery.

## Incremental plan and acceptance

1. [x] Define neutral descriptors, explicit operation metadata and compatibility facades.
2. [x] Move identity/session and transport differences into neutral adapters; retain
   service-level denials and existing local bootstrap/storage semantics.
3. [x] Preserve reusable Electron source/tests/notices in One; retire Core's
   desktop product entry, packaging workflow and obsolete user instructions.
4. [x] Qualify local and hosted presets: operation denial, identity races/rollback,
   foreign-store rejection, sessions, tenant/resource boundaries and broad regressions.
5. [x] Rebuild the development Core dependency in One and qualify signup/offline,
   data compatibility and packaged runtime. Replace with reviewed release pins
   only after the independent Core source/release review.

Preserve I-SUBSTRATE-01, I-EXT-01, I-GRAPH-01/02, I-ROLE-01/02, approval invariants
and I-DESKTOP-01's security/storage guarantees. I-DESKTOP-01 remains a stable
compatibility identifier; its enforcement now uses generic runtime capabilities.
No public release, signing operation, account deployment or Core push is implied.


## Host UI contract

Core's `host/session-contract.ts` defines the minimal session/profile interface;
the default `host/session.ts` returns no adapter. Products overlay the implementation
and account-return view. One implements the existing native bridge and offline
controls; Core contains no direct `window.integralDesktop` access. Hosted UI keeps
its normal account flow. Composition refuses replacement of the registry or session
contract. Host profile display cannot change Core principal/workspace authority.

Evidence: [18 October qualification](../ops/qualification/runtime-composition/2026-10-18.md). Production release pins remain a separate gate.
