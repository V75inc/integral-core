# Security and trust boundaries

Integral enforces access in backend services. This guide describes the posture to preserve; it is not a certification or a substitute for qualifying a deployment.

## Identity and scope

JWT identity comes from jvspatial authentication. Private workspace and resource access is checked server-side. Explicit scope must use `X-Integral-Scope: ws:<workspace_id>`. Client input, provider handles, prompts, and cached permission results cannot widen scope.

Direct and inherited grants differ. Exclusions remove inherited paths but cannot erase direct ownership or collaboration. Public reading and redeemed shares are narrow paths, not general write authority. Revocation must apply to tools, resumed work, and approval execution as well as browser reads.

## Application perimeter

Core must remain independent of domain App imports and identity-specific branches. Packages use declared capabilities and scoped facades. Review Python trust tier, signatures, path containment, extension assets, and dependencies before installation.

Skills are instructions, not grants. A model's request does not authorize a tool merely because it describes a persuasive workflow. Host extensions and outbound MCP connectors introduce explicit trust boundaries that operators must review.

## Data and keys

Keep JWT, OAuth, and credential encryption keys in a secret manager. Stored model credentials and private harness records need the appropriate encryption and retention policy. Keys must not reach client bundles, chat messages, audit output, or support logs.

Different audiences require separate resources. Integral has no general field-level visibility primitive. UI hiding and query filters cannot protect sensitive fields in an otherwise readable record.

## Execution and recovery

Current permission, revision checks, lease fencing, effect identity, and reconciliation govern effects. Approval does not establish successful execution. Unknown provider outcomes require investigation before a duplicate action.

Durable chat and public bounded work are separate qualification concerns. The latter has no qualified runnable public admission route. Production work needs PostgreSQL's required transaction/CAS and index guarantees; development storage behavior cannot establish production reliability.

## Operational hardening

Use TLS, explicit allowed origins, consistent CSP, configured email, active rate limits, restricted database access, backup/restore proof, and a real scanner where malware screening is required. The default attachment scanner is no-op. Keep DEBUG and TESTING disabled in production.

The [invariants](../INVARIANTS.md) and [qualification guide](QUALIFICATION.md) provide the engineering checks for these boundaries.
