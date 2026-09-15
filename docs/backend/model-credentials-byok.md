# BYOK model credentials (dual-mode LLM keys)

Integral's embedded jvagent harness supports **platform keys** (deployment env) and **per-user BYOK** (encrypted vault). Default is **hybrid**: owner's BYOK when configured, else `OPENAI_API_KEY`.

Scope: jvagent LLM calls only (orchestrator, reply, intro). Retrieval embeddings stay on the local sentence-transformers model.

---

## Quick start — operator (enable BYOK on a deployment)

### 1. Generate an encryption key

User keys are encrypted at rest. Production **requires** a dedicated 32-byte key (not `SECRET_KEY`):

```bash
openssl rand -base64 32
```

### 2. Set env in `backend/.env`

```bash
# hybrid (default) — platform keys unless workspace owner saved BYOK
INTEGRAL_AGENT_KEY_MODE=hybrid

# Required before users can save keys (non-DEBUG)
INTEGRAL_CREDENTIAL_ENC_KEY=<paste output from openssl>

# Platform fallback keys (hybrid mode, or when owner has no BYOK)
OPENAI_API_KEY=sk-...
```

### 3. Restart the backend

BYOK reads env at process start. After changing `INTEGRAL_CREDENTIAL_ENC_KEY` or `INTEGRAL_AGENT_KEY_MODE`, restart uvicorn / the container.

### 4. Pin jvagent

Requires **jvagent >= 0.1.6** (dual-provider BYOK: `light_provider`, `light_api_key`); `backend/uv.lock` is the source of truth — check there rather than trusting this line, which is a floor and not the pin.

---

## Quick start — end user (save your key)

1. Log in → **Settings** → **Agents** → **Model API key (BYOK)**.
2. Pick **provider** and **model** (recommended presets in dropdown).
3. Pick **light model** for cheap single-step turns (orchestrator gearing).
4. Optional: enable **different provider for light model** (e.g. OpenAI model + Anthropic Haiku) — requires a second API key.
5. Paste API key(s) → **Test connection** → **Save key**.

**Recommended pairs**

| Model | Light model |
|-------|-------------|
| OpenAI `gpt-4.1` | OpenAI `gpt-4o-mini` |
| OpenAI `o3-mini` | OpenAI `gpt-4.1-mini` |
| Anthropic `claude-sonnet-4-20250514` | Anthropic `claude-3-5-haiku-latest` |

Same provider uses one key for both tiers. Different providers need one key each.

Keys are **write-only**: UI shows fingerprint + `validated_at`, never the secret.

**Who pays:** the **workspace owner's** key powers every turn in workspaces they own. Org members chatting in the owner's org workspace use the **owner's** BYOK when configured.

### Voice input slot

**Voice input** (Settings → AI Models) is a fifth, opt-in slot. It powers the
chat composer's mic and the `integral_transcribe_audio` tool. It is **off**
until you set a model, because everyone in workspaces you own dictates on
this key. Only speech-capable providers qualify (`openai` today; the default
model is `gpt-live-transcribe`). With the same provider as your primary
model it reuses that key; with a different one it takes its own key. The
browser never receives the key — see [speech-input.md](speech-input.md).

---

## Deployment modes

| `INTEGRAL_AGENT_KEY_MODE` | Platform `OPENAI_API_KEY` | User BYOK | Typical use |
|---------------------------|---------------------------|-----------|-------------|
| `hybrid` (default) | Used when owner has no BYOK | Used when owner saved a key | SaaS default |
| `byo_strict` | Ignored for chat | **Required** (workspace owner) | BYO-only tenants; chat blocked until key saved |
| `platform_only` | Always | Ignored | Kill switch; central billing only |

`byo_strict` surfaces `model_key_required` on `GET /api/agentive/status` and blocks the chat composer until the workspace owner configures a key.

Voice input follows the same modes. It uses the platform `OPENAI_API_KEY`
only when `SPEECH_ALLOW_PLATFORM_KEY=true` (off by default), so dictation
never lands on the deployment's bill by accident.

---

## Resolution rule (per turn)

```
workspace_id → get_workspace_owner_user_id()
            → owner's UserModelCredential (if active)
            → decrypt → bind_model_override({
                provider, model, api_key,
                light_model?, light_provider?, light_api_key?
              })
            → jvagent interact_stream (one async task; ContextVar, not os.environ)
```

- Acting user ≠ billing user. Member in owner's org → owner's key.
- No BYOK + `hybrid` → `api_key_from_context()` falls back to env.
- Plaintext never stored in jvagent graph, `ChatTurnContext`, or ChangeEvents.

---

## Key rotation

```bash
# New primary
INTEGRAL_CREDENTIAL_ENC_KEY=<new 32-byte key>

# Old key still decrypts existing rows until re-saved
INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS=<old key>
```

Users re-save keys after rotation to re-encrypt under the new primary. `last_used_at` updates on each turn without touching the ciphertext blob.

---

## API (authenticated)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/users/me/model-credentials` | Metadata only (`key_fingerprint`, models, timestamps) |
| POST | `/api/users/me/model-credentials` | Body: `{ provider, model, api_key, light_*?, heavy_*?, vision_*?, speech_model?, speech_provider?, speech_api_key? }` — validate-on-save; `speech_model: ""` turns voice input off |
| DELETE | `/api/users/me/model-credentials` | Revoke active credential |
| POST | `/api/users/me/model-credentials/validate` | Test key without persisting |

Providers: `openai`, `anthropic` (validated via provider `GET /v1/models`).

Status probe: `GET /api/agentive/status` → `agent_key_mode`, `reason: "model_key_required"` when blocked in `byo_strict`.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| "BYOK is disabled" / save fails | `INTEGRAL_CREDENTIAL_ENC_KEY` unset in production |
| Chat works but always platform key | Owner has no saved BYOK, or `INTEGRAL_AGENT_KEY_MODE=platform_only` |
| `model_key_required` in chat | `byo_strict` and workspace owner has no key — Settings → Agents |
| Org member surprised by billing | Expected: owner's BYOK applies to whole workspace |
| Key save 400 | Provider rejected key — use Test connection; check key scopes |
| Mic missing in chat | Settings → Voice input: "Show the mic" off, or the browser supports no recognizer (Firefox without a workspace provider) |
| Voice input uses browser recognition, not OpenAI | Owner has no voice-input slot, caller is a workspace guest, or preference is "Browser recognition only" |

---

## Security

- AES-256-GCM (`v1:` envelope), dedicated `INTEGRAL_CREDENTIAL_ENC_KEY`
- `UserModelCredential` is an `Object` (I-GRAPH-02), not a graph `Node`
- Audit: `audit_snapshot_credential` — fingerprint only
- Account deletion purges credentials via `user_lifecycle`
- [security-review-model-credentials.md](security-review-model-credentials.md)
- [adr/001-model-credentials-byok.md](adr/001-model-credentials-byok.md)

## Related

- [ai-chat.md](ai-chat.md) — turn lifecycle and SSE proxy
- [workspace-agent-profile.md](workspace-agent-profile.md) — per-workspace skill overlay (separate from keys)
- [agent/README.md](../../agent/README.md) — harness bootstrap and `OPENAI_API_KEY`
