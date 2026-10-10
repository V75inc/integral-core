# Model credentials and BYOK

Integral resolves provider credentials on the server under host policy and the acting workspace/binding. Users manage credentials through the authenticated model-credential API; browser payloads must never receive a stored long-lived key.

`INTEGRAL_AGENT_KEY_MODE` supports hybrid, strict BYOK, and platform-only modes. Hybrid can use the applicable workspace-owner credential and permitted platform fallback. Strict BYOK rejects missing required credentials. Platform-only ignores stored BYOK for the configured route. Inspect the selected binding's resolver for exact provider behavior.

Credentials are encrypted using `INTEGRAL_CREDENTIAL_ENC_KEY`; previous-key configuration supports rotation. OAuth credentials use the separate jvspatial OAuth encryption key. Decryption authority stays in server services rather than App handlers or speech adapters.

Validate provider identity and route changes before invocation. Stored credential existence does not establish current provider validity. Keep keys out of logs, chat, manifests, diagnostics, and test artifacts.

Repair legacy duplicate UserModelCredential rows by querying through GraphContext before index creation. Test actual PostgreSQL duplicates and rejection of a new duplicate after repair. See the [deployment release gate](../ops/DEPLOY.md#jvspatial-011-release-gate).

The credential API stores one primary chat model and an optional speech-to-text model. Retired light/heavy/vision slots are rejected; no compatibility adapter or gear-switching configuration remains. The native resolver returns a direct model route and optional credential identity.
