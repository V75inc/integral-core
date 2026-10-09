# Speech input

Speech input turns spoken audio into text for the user to inspect and submit. Upload transcription and browser streaming use provider adapters with distinct transport requirements; speech input is not a general autonomous voice-agent contract.

Long-lived keys stay server-side. Streaming can return a short-lived transcription-only client secret with no-store response handling. Workspace guests cannot mint it. Key resolution and attachment access remain bound to the authorized workspace.

Platform-key fallback is disabled by default through `SPEECH_ALLOW_PLATFORM_KEY`. User preferences use the validated speech-preference route; provider policy is host/workspace configuration rather than arbitrary browser state.

Every streaming adapter origin must be permitted by all applicable CSP sources: backend headers and both nginx configurations. Keep microphone policy scoped to self. Test key absence from responses, cross-workspace refusal, expiry, malformed audio, cancellation, and actual transcription quality.

See [configuration](../ops/CONFIGURATION.md) and [credential policy](model-credentials-byok.md).
