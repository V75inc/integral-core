# Speech input (dictation) and transcription

Voice input in the agent chat composer, plus the `integral_transcribe_audio`
tool that transcribes stored audio attachments. Both run on the workspace's
speech-to-text provider.

## What the user gets

- **Mic button** beside the paperclip in the composer (dock and `/agent`).
  Click to toggle. **⇧⌘Space / Ctrl+Shift+Space** (rebindable): a quick tap
  toggles on, holding longer is push-to-talk.
- **Dictation, not voice turns.** Live interim text appears in the message
  box and is replaced as it firms up. The user reviews it and sends as usual.
  "Send when I stop talking" is an opt-in preference, off by default, and it
  never fires while the agent is still answering.
- **Escape** cancels and restores the message box as it was. **Typing** while
  dictating stops dictation and keeps what was said.
- **Settings → Voice input**: enable, recognizer (workspace provider or
  browser only), language, shortcut and its behavior, silence auto-stop,
  auto-send, a local microphone test, and the workspace provider's status.

## Recognizers

| Engine id | Where it runs | Key | Notes |
|---|---|---|---|
| `openai-realtime` | Browser ↔ OpenAI over WebRTC | Workspace owner's `speech` credential slot, or the platform key if the operator allows it | Transcription-only Realtime session |
| `webspeech` | The browser's Web Speech API | none | Chrome and Edge send audio to Google and Microsoft, Safari to Apple; Firefox has none. The UI shows this privacy note |

`GET /api/agentive/speech/config` returns the engines the caller may use in
the current workspace, workspace provider first. The browser picks the first
one it supports (`features/speech/resolveEngine.ts`). A `browser` preference
never falls back to the workspace provider. **Workspace guests** get the
browser engine only — they don't spend the workspace's provider quota.

## Where the key comes from

The provider is configured as the **`speech` slot on the owner's model
credential** (Settings → AI Models → Voice input), next to the
light/heavy/vision slots. See
[model-credentials-byok.md](model-credentials-byok.md) and ADR-001. The slot
is opt-in: an empty model means off. It reuses the primary key when the
provider matches, and otherwise stores its own encrypted key.

`resolve_speech_credential(workspace_id)` in
`services/model_credential_resolver.py` applies `INTEGRAL_AGENT_KEY_MODE`:

- `hybrid` — the owner's speech slot, else the platform key.
- `byo_strict` — the owner's slot only.
- `platform_only` — the platform key only.

The platform key (`OPENAI_API_KEY`) is used for speech **only** when
`SPEECH_ALLOW_PLATFORM_KEY=true`, because dictation on it bills the
deployment for every workspace.

## Security model

- **The provider key never reaches the browser.** `POST
  /api/agentive/speech/session` mints a transcription-only client secret
  (`SPEECH_SESSION_TTL_SECONDS`, default 60) and returns it with
  `Cache-Control: no-store`. The browser uses it once to open the session.
  No audio passes through Integral.
- The credential is checked only when the session opens. Session length is
  therefore capped client-side (`SPEECH_MAX_SESSION_SECONDS`), and minting is
  rate-limited per user (`SPEECH_SESSION_RATE_LIMIT`, in-process — see
  ADR-005).
- Every mint logs one structured line: user, workspace, provider, model and
  credential source. It isn't a ChangeEvent, because nothing in the graph
  changes.
- **Headers.** `Permissions-Policy: microphone=(self)`. The provider's
  browser origin must be in `connect-src` in **all three** header sources:
  `backend/app/middleware/security_headers.py` and both nginx templates. The
  origin is a literal placed before `${CSP_EXTRA_CONNECT}`, so an env
  override can't drop it. `tests/test_speech_registry.py` and
  `tests/test_security_headers_speech.py` fail if an adapter's origin is
  missing.
- **The transcription tool** reads the bound workspace's key only (PC-2 —
  no `workspace_id` parameter). The attachment must belong to that
  workspace, and it applies the attachment's own read gate. Transcripts are
  marked `content_untrusted`.

## Adding a provider

1. **Backend adapter**: a class in
   `backend/app/agentive/services/speech/providers/` implementing
   `SttProvider` (`base.py`). Pure HTTP only — it gets a `ProviderContext`
   with the resolved key. Declare `capabilities` (streaming / batch,
   `client_engine`, `browser_connect_origins`, batch limits) and list the
   adapter in `providers.builtin_providers`.
2. **Credential source**:
   - An LLM vendor that also transcribes: add its slug to `SpeechProvider`
     in `app/schemas/model_credentials.py` and to `SPEECH_CAPABLE_PROVIDERS`
     in `frontend/src/api/modelCredentials.ts`. Add model presets to
     `SPEECH_MODEL_PRESETS`.
   - A speech-only vendor (Deepgram, AssemblyAI, …): `credential_source =
     "connector"`. This needs the connector-catalog `kind: service` work
     tracked as ADR-011, and isn't built yet.
3. **Headers**: add its browser origins to `SPEECH_CONNECT_ORIGINS` and both
   nginx `connect-src` literals. The parity tests fail until you do.
4. **Frontend engine**: an `SttEngine` in `frontend/src/features/speech/engines/`
   whose `id` equals the adapter's `client_engine`, added to the registry's
   built-ins. Engines emit `interim` / `final` / `end` events. The composer,
   the hook and Settings don't change.

Batch-only providers (no live streaming) plug in through the reserved
`server-batch` engine seam: record in the browser, then upload.

## Operations

- Use a dedicated OpenAI project key with Realtime and audio access, and set
  budget alerts on that project.
- Knobs (`app/config.py`):
  - `SPEECH_ALLOW_PLATFORM_KEY`, `SPEECH_STREAM_MODEL_DEFAULT`,
    `SPEECH_BATCH_MODEL_DEFAULT`;
  - `SPEECH_SESSION_TTL_SECONDS`, `SPEECH_MAX_SESSION_SECONDS`;
  - `SPEECH_SESSION_RATE_LIMIT`, `SPEECH_TRANSCRIBE_RATE_LIMIT`;
  - `SPEECH_TRANSCRIBE_MAX_BYTES`, `SPEECH_TRANSCRIBE_TIMEOUT_SECONDS`.
- Networks that block WebRTC (UDP) make the OpenAI engine fail with a
  "network" error. Users can switch to browser recognition in Settings.
