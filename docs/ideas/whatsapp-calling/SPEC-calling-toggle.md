# Spec: WhatsApp calling toggle

Status: implemented

This is the settings writer for `UsesWhatsAppLine.calling_enabled`. Call admission already reads that flag. Turning calling on also enables WhatsApp Cloud calling on the bound JV Connect number and reveals the jvvoice URL and API key for that line.

## Objective

A workspace owner or admin turns WhatsApp calling on or off from the existing WhatsApp connectors dialog, using the same pill `ToggleRow` as other Integral settings, and sees the saved value after reload. The value is the `calling_enabled` boolean on the workspace's `UsesWhatsAppLine` edge (`backend/app/agentive/edges.py`). It defaults to false. Text messaging stays on `grant.enabled` and keeps working when calling is off.

When calling is on:

- Integral POSTs `{enabled}` to JV Connect `POST /api/v1/meta/whatsapp/calling/settings` so Meta calling matches the toggle. A JV Connect failure is 503 and the Integral edge stays unchanged.
- The dialog reveals a Voice service panel: jvvoice HTTPS URL and API key. Those are stored on `WhatsAppChannelBinding` (ciphertext + last4). Env `JVVOICE_BASE_URL` / `JVVOICE_API_KEY` remain the fallback when the line has no override.
- LiveKit `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` stay on the jvvoice host. Integral never collects them.

The existing jvconnect base URL and jvconnect API key fields stay; those are the customer JV Connect line.

Out of scope: a new connector, a Settings section, a jvvoice catalog card, a second dialog, spoken refusal, and any change to accept or disconnect besides reading the saved jvvoice URL and key.

## Commands

From the repo root:

```bash
cd frontend && npx vitest run src/components/settings/__tests__/WhatsAppSettings.test.tsx src/features/settings/sections/__tests__/ConnectorsSection.test.tsx
cd backend && pytest tests/test_whatsapp_call_admission.py tests/test_whatsapp_ingress.py -m unit
cd backend && pytest tests/test_whatsapp_calling_toggle.py tests/test_whatsapp_channel_binding.py tests/test_jvconnect_whatsapp_client.py tests/test_connector_package_discovery.py::test_whatsapp_package_does_not_copy_grants tests/test_whatsapp_ingress.py::test_verified_text_is_accepted_without_a_reply -q
```

In jvconnect:

```bash
npx vitest run tests/unit/v1WhatsappCallingSettingsRoute.test.ts tests/unit/openapiDocument.test.ts
```

The `-m unit` command is the admission and text-parser bar. `unit` skips the per-test database (`backend/tests/conftest.py`), so the new persistence file must not be marked `unit`. Format the touched Python with `black`, `isort`, and `flake8` before the graph command is treated as done.

No live Meta, jvconnect, jvvoice, or LiveKit process is required.

## Project Structure

Allowed to change:

- `backend/app/agentive/api/channels.py` — extend the existing binding GET; calling PUT plus `/binding/calling/service`.
- `backend/app/agentive/services/whatsapp_channel_binding.py` — `set_workspace_line_calling_enabled`, `set_workspace_line_voice_service`, `workspace_calling_view`. Do not edit the body of `set_workspace_line_enabled`.
- `backend/app/agentive/clients/jvconnect_whatsapp.py` — `fetch_calling_settings` / `set_calling_settings`.
- `backend/app/agentive/services/whatsapp_call_control.py` — prefer the line's saved jvvoice URL and key, then env.
- `backend/app/agentive/nodes.py` — jvvoice URL, ciphertext, last4 on `WhatsAppChannelBinding`.
- `backend/app/schemas/agentive/whatsapp_channel.py` — calling and voice-service request models. Leave `SaveWhatsAppChannelBindingRequest` unchanged (`extra: forbid`).
- `backend/tests/test_whatsapp_calling_toggle.py` — graph tests.
- `frontend/src/api/agentive.ts` — binding fields and clients.
- `frontend/src/components/settings/WhatsAppSettings.tsx` — `ToggleRow` plus the revealed Voice service panel.
- `frontend/src/features/settings/components/Field.tsx` — `ToggleRow` `disabled` / `role="switch"`.
- `frontend/src/components/settings/__tests__/WhatsAppSettings.test.tsx` — dialog behavior.
- `docs/backend/connectors.md` — one paragraph under "WhatsApp (JV Connect)".
- `jvconnect` v1 `GET/POST /api/v1/meta/whatsapp/calling/settings`.

Forbidden:

- `backend/app/connectors/bundled_packages/whatsapp/adapter/grants.py` — must not gain the string `calling_enabled`. `grant_status` stays `{edge, present, id, enabled}` with `id` equal to the binding id. `test_whatsapp_package_does_not_copy_grants` asserts that dict.
- The body of `set_workspace_line_enabled`, through the next `async def`. `test_uses_whatsapp_line_calling_enabled_defaults_false` asserts that slice does not contain `calling_enabled`.
- `public_binding_view` — remains the binding-node status. It does not receive the edge and does not grow calling fields. Credential PUT and DELETE keep returning it.
- `backend/app/config.py` and every LiveKit setting.
- `disconnect_workspace_whatsapp_line` and personal `ChannelIdentity` deletion.
- A new route family, a Settings page, a catalog card, or a second dialog.
- Collecting LiveKit URL, API key, or secret in Integral.

## Code Style

Match the binding routes in `channels.py` and the workspace fields in `WhatsAppSettings.tsx`. Schemas live in `backend/app/schemas/agentive/`. Errors are `BadRequestError` (400), `InsufficientPermissionsError` (403), `ResourceNotFoundError` (404), `ResourceConflictError` (409), and `ServiceUnavailableError` (503) from `app.api.errors`. No `APIRouter` and no `HTTPException`.

Use `ToggleRow` from `frontend/src/features/settings/components/Field.tsx` for Voice calling (`role="switch"`). When the switch is on, show the Voice service panel with `Field` + `Input` for jvvoice URL and key, plus Save. "Save changes" remains the jvconnect credential PUT and must not send `calling_enabled`.

## API

### GET `/api/agentive/channels/whatsapp/binding`

When `binding` is null, the body stays `{binding: null, grant: grant_status(None)}`.

When a line is granted, `binding` is `public_binding_view` plus:

| Field | Source |
| --- | --- |
| `calling_enabled` | `bool(edge.calling_enabled)`, false when the attribute is missing |
| `calling_grant_id` | `edge.id` |
| `calling_revision` | `int(edge.revision)` |
| `jvvoice_base_url` | binding override, empty when unset |
| `jvvoice_api_key_last4` | last four of the stored jvvoice key, empty when unset |
| `jvconnect_calling_status` | best-effort `ENABLED` / `DISABLED` from JV Connect, empty on failure |
| `jvconnect_calling_error` | empty on success; set when calling settings cannot be read |

`grant` is unchanged. Credential PUT and DELETE keep returning `public_binding_view` only.

### PUT `/api/agentive/channels/whatsapp/binding/calling`

Request:

```json
{
  "calling_enabled": true,
  "expected_grant_id": "<UsesWhatsAppLine.id>",
  "expected_revision": 0
}
```

`extra: forbid`. LiveKit fields, `grant.enabled`, and jvconnect credentials are 400.

Authorization and conflicts, in this order, inside `_workspace_line_write` after `_require_line_admin`:

1. No workspace: 400 `A workspace is required`.
2. Caller is not owner or admin: 403.
3. This workspace has no line: 404 `WhatsApp channel is not configured`.
4. The edge is missing, `edge.id` differs from `expected_grant_id`, or `edge.revision` differs from `expected_revision`: 409.
5. POST JV Connect calling settings. Failure: 503, Integral edge unchanged.
6. If the stored boolean already equals the request: 200, do not increment `revision` (Meta was still updated).
7. Otherwise set `calling_enabled`, add 1 to `revision`, save the edge.

Do not assign `grant.enabled`. Repeat PUT of the same boolean still syncs Meta so a retry can repair it.

### PUT `/api/agentive/channels/whatsapp/binding/calling/service`

Body `{jvvoice_base_url, api_key}`, `extra: forbid`. HTTPS URL or empty. Empty URL and empty key clears the override. A URL without a key is 400 unless a key is already stored. LiveKit fields are 400.

## Dialog

The control lives in the workspace section of `WhatsAppSettings`. It is absent when `binding` is null. When a binding is loaded it shows `ToggleRow` "Voice calling", checked from `binding.calling_enabled`.

`canEditWorkspace` stays `canManageLine && !bindingLoadFailed && dialog?.mode !== 'View'`. The switch is enabled only when that is true and `calling_grant_id` is non-empty.

On change, PUT the boolean with the grant id and revision from the last GET or calling response. Then reload GET. A 409/403/503 toasts and reloads.

When calling is on, the Voice service panel appears. It never shows LiveKit env names or fields. Save voice service PUTs the jvvoice URL and key.

Disconnect stays `disableWhatsAppChannelBinding(binding.id)`. It does not unlink personal numbers.

## Testing Strategy

Frontend: Vitest in `WhatsAppSettings.test.tsx`.

- Unchecked `role="switch"` named Voice calling; click sends `calling_enabled: true`. After GET with true, the switch is checked and the jvvoice fields appear. Clicking off sends false.
- Members and View mode: switch disabled, no PUT.
- `binding: null`: no Voice calling control.
- jvvoice fields are hidden until calling is on. The document has no `LIVEKIT_URL`, `LIVEKIT_API_KEY`, or `LIVEKIT_API_SECRET`. Saving voice service sends the jvvoice URL and key.

Backend: `tests/test_whatsapp_calling_toggle.py` (asyncio, not `unit`).

- GET starts with calling off.
- PUT true then false updates the edge and revision. Same-boolean PUT still syncs Meta and does not increment revision.
- JV Connect failure is 503 and the edge stays false.
- Voice service PUT stores last4 and rejects LiveKit extras.
- Non-admin 403. Stale revision 409. Extra fields on the calling PUT are 400.

## Boundaries

- Always: read and write calling through the workspace `UsesWhatsAppLine` edge; require owner or admin; compare edge id and integer revision; leave `grant.enabled` and `set_workspace_line_enabled` alone; leave `grants.py` and `grant_status` alone; fail closed if JV Connect calling cannot be updated; keep LiveKit values out of Integral.
- Ask first: a new route outside `/agentive/channels/whatsapp/binding`; putting `calling_enabled` on `public_binding_view` or on the `grant` object; collecting LiveKit keys in Integral.
- Never: revert the connectors dialog wiring; add a Settings section, catalog card, or second dialog; fold calling into `set_workspace_line_enabled` or the credential PUT; delete personal number links on disconnect or on this toggle; mark the graph tests `unit`.

## Success Criteria

1. An owner or admin turns calling on and off from the WhatsApp dialog using the Integral pill switch. After reload, GET returns `binding.calling_enabled`, `calling_grant_id`, and `calling_revision`.
2. Enabling calling turns Meta calling on through JV Connect. A JV Connect failure leaves the Integral edge off.
3. When calling is on, the dialog shows jvvoice URL and API key. Saving stores last4. LiveKit fields are rejected.
4. A non-admin PUT receives 403. View mode and `canManageLine={false}` do not send the PUT.
5. A stale `expected_grant_id` or `expected_revision` receives 409. A repeated PUT of the current boolean does not increment `revision` but still syncs Meta.
6. `calling_enabled` false still admits text and still refuses calls. `grant.enabled` is unchanged by the calling write.
7. `set_workspace_line_enabled` and `grants.py` still do not contain `calling_enabled`.
8. Disconnecting the line does not unlink personal numbers.
9. `ConnectorsSection.test.tsx` passes without removing the WhatsApp dialog wiring.
