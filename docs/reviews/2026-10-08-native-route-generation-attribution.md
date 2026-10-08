# Native model route generation attribution

## Shared credential plane

Native route resolution opts into generation attribution on the existing workspace-owner credential lookup. It obtains the key and reference from the same resolved record, avoiding a second lookup that could observe a different rotation. The opaque reference hashes the stored record ID, provider/model, encrypted default-slot envelope and configuration update stamp. Plaintext keys and display fingerprints are not used or persisted in observations. Last-used telemetry does not change the generation; replacement or re-encryption conservatively does. Compatibility harness callers retain the original override shape.

Deployment-managed native model routes accept optional `INTEGRAL_NATIVE_CREDENTIAL_REF` from trusted server environment. This must be a bounded, canonical, nonsecret generation identifier maintained by the operator and changed on credential rotation. Compose forwards it to the API. Missing attribution remains unknown: ordinary chat is available, while exact bound mandate settlement cannot use an unattributed receipt. This setting does not configure a key, supply a price or establish provider billing authority.

For keyless Ollama profiles, a stored workspace model configuration does not identify the credentials held by a remote daemon. Attribution therefore requires an operator-owned daemon generation and combines it with the profile generation and normalized endpoint. The endpoint and either generation changing produce a different reference. Cloud models accessed through an Ollama daemon are not assumed free.

## Accounting boundary

The existing SDK observer retains the resolved reference in encrypted intent and terminal observations. The bound reader and internal settlement validate exact saved route and scope. Historical unattributed observations are not relabeled. Operators must update the deployment reference when external credentials rotate; Core cannot infer rotation inside a remote daemon or independently authenticate an operator's billing claims.

No price quote resolver, physical mandate admission, public executable approval, child producer or founder run control is enabled by this change. Provider-reported cost may still be unavailable. Internal receipt settlement introduced in `3cb0a6a9` remains distinct from a billing invoice ledger and live provider qualification.

## Verification scope

Resolver tests cover keyed BYOK, keyless profiles, platform/local environment references, invalid references, endpoint change, stored generation rotation and telemetry stability. Existing PostgreSQL receipt/mandate cases exercise encrypted evidence, ownership, route mismatch, cancellation, duplicate settlement and unknown-cost hold retention. Their approvals and provider receipts are synthetic; no external request is made. Full repository verification, deployment and browser evidence are recorded separately when performed.
