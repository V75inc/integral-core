# AI models, providers and BYOK

Settings → AI Models provides a simple setup: choose a provider, add its key,
and use the provider's model default. Advanced configuration exposes Models and
Providers. Models groups Language & multimodal, Embeddings and Voice in a
single-open accordion; Language & multimodal is collapsed initially.

## Providers and model profiles

A private provider connection owns an encrypted key and immutable provider/endpoint
identity. Profiles select that connection, a model ID and supported tuning.
Changing provider or endpoint requires a new connection. Editing or testing an
existing connection never returns its key to the browser. Legacy active
credentials appear as an idempotent connection/profile snapshot; reading settings
does not activate or rewrite them. Activation maintains the legacy API projection.

Supported controls include reasoning effort or thinking budget, output tokens,
temperature/top-p, verbosity and applicable local context-window settings. The
server and browser use the same model capability contract; unsupported settings
are rejected. Request/idle timeouts, request/tool counts, total tokens and turn
time can lower deployment ceilings, never raise them. Unset values inherit
provider/deployment defaults. Testing a provider establishes access, not support
for every model, tool or tuning parameter. All update feedback uses toasts.

## Single or automatic routing

Single model is the default. A workspace owner may select Automatic (light +
heavy), assigning an owned Light profile and the default Heavy profile. Light
handles conversation without workspace tools; operational work escalates to
Heavy. Both roles share one principal, turn budget, deadline, approval boundary
and operational checkpoint. There is no peer-agent or delegation layer.

Saving a profile does not enable routing. Save the routing configuration to
activate the pair. Assignment validates ownership, credential/endpoint identity
and tool support, using revision checks. Assigned or conversation-referenced
profiles cannot be removed until reassigned; referenced providers cannot be
removed. Key or route changes are rechecked before physical model dispatch.

The chat's brain menu selects Automatic, Heavy only or a saved profile for that
conversation. Its collapsed Reasoning section exposes only settings supported by
the selected models. Per-role overrides live on the thread, never contain keys,
and do not modify shared profiles or operator ceilings. Model changes clear
incompatible conversation reasoning choices. Reset restores workspace routing.

## Deployment settings and funding

`INTEGRAL_AGENT_KEY_MODE` selects `hybrid`, `byo_strict` or `platform_only`.
Resolution uses the current workspace owner's configuration, not an arbitrary
member's personal key. Hybrid permits the owner's saved model to override ENV
settings and uses permitted platform fallback otherwise. Strict BYOK requires
that owner's usable credential; it never silently borrows a platform key.
Platform-only ignores personal model routes and exposes server-managed settings.
ENV keys are never copied into personal Providers.

Deployment routing uses:

- `INTEGRAL_NATIVE_ROUTING_MODE=single|light_heavy`.
- `INTEGRAL_NATIVE_MODEL` for Heavy/default and `INTEGRAL_NATIVE_LIGHT_MODEL`
  for optional Light, using the supported provider/model route syntax.
- `INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS` and
  `INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS` as validated JSON tuning objects.
- `INTEGRAL_NATIVE_CREDENTIAL_REF` for the operator credential identity.

The ENV readout describes inherited settings. Under hybrid or strict BYOK,
workspace owners can add Providers and profiles through settings to override
them. See [deployment](../ops/DEPLOY.md#optional-native-lightheavy-model-roles)
for operator ceilings and [resident chat](ai-chat.md) for execution and timing.

## Security and persistence

Credentials use `INTEGRAL_CREDENTIAL_ENC_KEY`; previous-key configuration supports
rotation. OAuth has its separate jvspatial key. Keys never enter chat, manifests,
logs or public diagnostics. Private scalar configuration uses scoped `Object`
records; service-layer writes enforce current authority and revision checks.
Account deletion removes provider/profile records with the legacy credential.

Authenticated `/users/me/` APIs expose `ai-configuration`, `model-capabilities`,
`model-connections` (save/test/remove), `model-profiles` (save/activate/remove),
`model-routing` and `conversation-model`. Responses whitelist safe metadata.
The legacy primary/speech credential API remains a compatibility projection;
its obsolete slot names are not the new profile-based routing contract.

When repairing duplicate `UserModelCredential` records, query through
`GraphContext.find` before creating a unique index. Test real PostgreSQL duplicates
and rejection of another duplicate after repair. See the
[release gate](../ops/DEPLOY.md#jvspatial-compatibility-release-gate).

Voice preferences and provider-backed/local-default embeddings are documented in
[voice and embedding models](voice-and-embedding-models.md).
