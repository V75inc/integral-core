# Spec: call-control

Module id: `call-control`. Depends on `call-admission`.

## Objective

When jvconnect forwards a signed Meta `calls` webhook for the existing workspace line, Integral decides with `call-admission`, fetches the Meta calling token from jvconnect, and tells jvvoice to accept or disconnect. A refused caller never receives an accept. This module does not transcribe, speak, or run the resident harness.

The caller is the verified owner, or someone else ringing the same business number. Success for the owner is one jvvoice accept after admission says `answer`. Success for everyone else is zero accept calls. Success for the automated bar is those outcomes against mocked HTTP, not a handset.

Today a `calls` payload never reaches that decision. `parse_whatsapp_envelope` reads only `value.messages` (`backend/app/agentive/services/whatsapp_ingress.py`). A calls envelope has no messages, so it becomes `kind="ignore"`, and `_dispatch_verified` returns `{"ok": True}` without admitting anyone. jvconnect already forwards `calls` (`c:\v75\jv\jvconnect\lib\webhookForwards.ts`, `AGENT_FORWARD_FIELDS` includes `calls`; `shouldForwardWebhookToAgents` forwards a `calls` array or call `statuses`). The raw Meta body is what Integral verifies.

## Assumptions

1. The webhook stays `POST /api/agentive/channels/whatsapp/webhook` (`backend/app/agentive/api/channels.py`, `@endpoint`, `auth=False`). Signature headers stay `x-jvconnect-signature` then `x-hub-signature-256`. Verification stays `verify_jvconnect_signature`. A bad signature still raises `MissingAuthenticationError` and performs no outbound call.
2. jvconnect signs the raw body with HMAC-SHA256 and sends `sha256=` plus the hex digest on both headers (`lib/webhookForwards.ts`, `signWebhookBody`). Integral already checks that shape.
3. The calls object Integral acts on is the first `entry[].changes[]` whose `field` is `calls`, then `value.calls[0]`, matching the fields jvconnect already types: `id`, `event`, `from`, `session.sdp`, `session.sdp_type` (`lib/webhookIngress.ts`). `value.metadata.phone_number_id` selects the binding, as `_payload_phone_number_id` does for messages.
4. Accept requires a non-empty call id, a caller, and a non-empty `session.sdp`. Status-only call payloads and events without SDP do not accept. Unknown `event` strings do not change that rule. Exact Meta event names are not coded as a whitelist until they are read from a captured payload.
5. Admission runs before any jvconnect credential fetch and before any jvvoice call. `refuse` means the accept URL is not requested.
6. Calling credentials are `GET /api/v1/meta/whatsapp/calling/credentials` on the binding's `jvconnect_base_url`, with the binding's existing API key as `Authorization: Bearer`. The route returns `phone_number_id`, `access_token`, and `waba_id` (`c:\v75\jv\jvconnect\app\api\v1\meta\whatsapp\calling\credentials\route.ts`). The client follows `fetch_account` in `backend/app/agentive/clients/jvconnect_whatsapp.py` (`_call`, `_endpoint`, `client=` for tests).
7. The Meta `access_token` is passed through as jvvoice's existing `whatsapp_api_key` field and then dropped. It is not written onto `WhatsAppChannelBinding`, not put in `public_binding_view`, and not logged. `_log_result` already logs status only.
8. jvvoice accept and disconnect stay `POST /api/calls/accept` and `POST /api/calls/disconnect` with `Authorization: Bearer` equal to `JVVOICE_API_KEY` (`c:\v75\jv\jvvoice\api.py`, `api_auth.py`). Integral holds that key and `JVVOICE_BASE_URL` as deployment settings, the same handling as the jvconnect key: present only on the outbound `Authorization` header, never in a public binding view, never logged. It is not a second copy inside each binding ciphertext, because jvvoice has one server key, not a per-line customer key. The base URL uses the existing outbound URL check (`validate_outbound_http_url` in the jvconnect client).
9. LiveKit `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` stay on jvvoice. Integral does not read them.
10. The accept body for this path does not send placeholder `jvagent_agent_id` or `jvagent_base_url`. Today's `AcceptCallRequest` requires both (`jvvoice/api.py`). The body `voice-bridge` specifies is the one `call-control` sends. Mocked tests assert that body. A live jvvoice rejects it until `voice-bridge` lands. That is expected.
11. jvconnect may deliver the same body more than once (`DEFAULT_AGENT_FORWARD_MAX_ATTEMPTS` is 3 in `webhookForwards.ts`). One `WhatsAppInboundReceipt` (`backend/app/models/whatsapp_inbound.py`, an `Object`, kind `call`, `meta_message_id` set to the call id) makes a retry return success without a second accept. This mirrors the message-id check in `_dispatch_verified`.
12. On refuse, Integral calls disconnect and does not call accept. Disconnect uses `user_initiated: false` so jvvoice sends `BUSINESS_INITIATED` (`connector_client.py`). The Meta token is attached when it was fetched. If admission refuses before a token fetch, disconnect is still attempted only when a token is already in hand; otherwise the webhook returns after recording `refused` and does not accept. A disconnect error is logged as a status and does not cause a later accept.
13. The webhook returns when accept or the refuse path finishes. It does not wait for the realtime conversation. `voice-bridge` starts only after accept returns `status: "connected"`.
14. Text envelopes keep the current parser and dispatcher. A calls envelope must not fall through to `_TEXT_ONLY_NOTICE` ("Text only for now"), which is only for `kind="non_text"` messages.

## Tech Stack

- Existing Integral backend, httpx client style in `jvconnect_whatsapp.py`, `@endpoint` already on the webhook.
- jvconnect calling-credentials route, read only, no jvconnect code change required for the fetch.
- jvvoice HTTP API as it exists, plus the accept-body extension specified in `SPEC-voice-bridge.md`.
- Schemas for any new request or response model live in `backend/app/schemas/agentive/`. The webhook body is the raw Meta forward, not a new public JSON API.
- Errors: `MissingAuthenticationError` for a bad signature, `BadRequestError` for an oversized body (already `1_000_000` bytes in `channels.py`). Provider failures become a recorded receipt and a non-accept outcome, not `HTTPException`.

## Commands

From `backend/`:

```bash
uv sync --frozen --extra dev --extra test
pytest tests/test_whatsapp_call_control.py tests/test_whatsapp_ingress.py tests/test_jvconnect_whatsapp_client.py -v -m unit
black app/agentive/services/whatsapp_ingress.py app/agentive/services/whatsapp_call_control.py app/agentive/clients/jvconnect_whatsapp.py tests/test_whatsapp_call_control.py
isort app/agentive/services/whatsapp_ingress.py app/agentive/services/whatsapp_call_control.py app/agentive/clients/jvconnect_whatsapp.py tests/test_whatsapp_call_control.py
flake8 app/agentive/services/whatsapp_ingress.py app/agentive/services/whatsapp_call_control.py app/agentive/clients/jvconnect_whatsapp.py tests/test_whatsapp_call_control.py
```

No live Meta, LiveKit, jvconnect, or jvvoice process is required for this bar.

## Project Structure

```
backend/app/agentive/services/whatsapp_call_control.py
    Orchestrate admission, credential fetch, jvvoice accept or disconnect, receipt.
backend/app/agentive/services/whatsapp_ingress.py
    Branch a signed calls payload into call-control before the ignore/text dispatcher.
backend/app/agentive/clients/jvconnect_whatsapp.py
    fetch_calling_credentials, same _call path as fetch_account.
backend/app/agentive/api/channels.py
    Unchanged route. Still the only WhatsApp webhook.
backend/app/schemas/agentive/whatsapp_call.py
    Pydantic models for the jvvoice accept and disconnect bodies Integral sends,
    if those bodies are validated locally before the HTTP call.
backend/tests/test_whatsapp_call_control.py
    Mocked HTTP. Marker unit where the graph is not required.
docs/ideas/whatsapp-calling/SPEC-call-control.md
    This spec.
```

Do not add `APIRouter` under `backend/app/`. Do not add a connector package. `connector.yaml` stays `id: whatsapp` and `mode: managed-text-v1`.

## Code Style

Extend the jvconnect client the way `fetch_account` is written. Pass `client=` so tests inject `httpx.MockTransport`, as in `backend/tests/test_jvconnect_whatsapp_client.py`.

```python
async def fetch_calling_credentials(
    base_url: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """GET the Meta token jvvoice needs for AcceptWhatsAppCall.

    The access_token stays in the result data for the caller to forward.
    This function does not log or store it.
    """
    return await _call(
        "GET",
        _endpoint(base_url, "calling/credentials"),
        api_key,
        client=client,
        operation="calling credentials",
    )
```

Call-control orchestration is async and explicit: admit, then fetch, then accept, then record. A refuse returns before `fetch_calling_credentials` unless a token is required for disconnect; it never calls the accept function. Log operation names and HTTP status codes. Do not log `Authorization`, `access_token`, `whatsapp_api_key`, SDP, or the jvvoice bearer.

jvvoice requests use a small httpx helper with the same timeout and "do not log the bearer" rule. The helper takes an injected client in tests.

## Testing Strategy

Framework: pytest plus `httpx.MockTransport`, same helper shape as `test_jvconnect_whatsapp_client.py`. Admission is the real `admit_whatsapp_caller` with in-memory inputs, or a fake that returns a fixed `CallerAdmission`. No live call.

Levels:

- Unit: signature rejection performs zero outbound requests. Parser distinguishes a calls envelope from a text envelope and from a status-only calls envelope.
- Unit: credential client hits `GET {base}/api/v1/meta/whatsapp/calling/credentials` with the bearer, returns `access_token`, and the captured logs do not contain the token or the API key. Copy the log assertion style from `test_register_webhook_posts_callback_and_omits_secret_from_logs`.
- Unit, mocked jvvoice: when admission is `answer`, exactly one `POST /api/calls/accept` is made, the JSON includes `whatsapp_call_id`, `phone_number_id`, `whatsapp_api_key` (the Meta token), `sdp`, `sdp_type`, and `caller_phone`, and it omits `jvagent_agent_id` and `jvagent_base_url`. The bearer is the jvvoice key.
- Unit, mocked jvvoice: when admission is `refuse`, the accept URL is never requested. If disconnect is invoked, it is `POST /api/calls/disconnect` with `user_initiated` false.
- Unit: a second delivery of the same call id does not produce a second accept.
- Unit: credential HTTP 500, invalid JSON, or a jvvoice body whose `status` is not `connected` produces no bridge handoff and no second accept.
- Regression: `tests/test_whatsapp_ingress.py` still passes, including text and non-text envelopes. A calls fixture must not store a `non_text` receipt or send "Text only for now".

The automated bar stops at mocked HTTP. A real WhatsApp ring is not evidence this module requires, and a passing mock is not evidence of a real call.

## Boundaries

- Always: verify the signature before parsing; run admission before any provider call; refuse with zero accepts; keep the Meta token and the jvvoice key out of logs and binding documents; keep LiveKit keys on jvvoice; leave the text path in place; make accept idempotent per call id.
- Ask first: a new public route besides the existing webhook; persisting `calling_enabled`; changing jvvoice's URL or auth scheme; storing the Meta token.
- Never: add a connector id or a customer principal. Never dial outbound. Never send dummy jvagent fields. Never accept after `refuse`. Never terminate WhatsApp media inside Integral. Never call Deepgram, ElevenLabs, or jvagent `/interact` from this module. Never treat a spoken "yes" as authorization. Never put `LIVEKIT_*` in Integral settings. Never use `APIRouter` or `HTTPException` in `backend/app/`.

## Success Criteria

1. A forged or missing `x-jvconnect-signature` / `x-hub-signature-256` raises `MissingAuthenticationError` and the test's HTTP mock records no jvconnect or jvvoice request.
2. A signed envelope whose first change is `field: "calls"` with `calls[0].id`, `from`, and `session.sdp` is handled by call-control. It is not `kind="ignore"` and it does not send "Text only for now".
3. A signed text message still follows `parse_whatsapp_envelope` and the existing dispatcher. Existing ingress tests pass.
4. Admission `answer` causes one `GET .../calling/credentials` and, only if that result is OK and `access_token` is non-empty, one `POST {JVVOICE_BASE_URL}/api/calls/accept`. The accept JSON carries the Meta token as `whatsapp_api_key` and does not contain `jvagent_agent_id` or `jvagent_base_url`.
5. Admission `refuse` produces zero requests to `/api/calls/accept`, including when disconnect itself returns an error.
6. Logs and `public_binding_view` contain neither `access_token` nor `JVVOICE_API_KEY`. The token is absent from `WhatsAppChannelBinding` after the request.
7. A repeated webhook with the same call id results in one accept across both deliveries.
8. jvvoice `status` other than `connected`, or a credential failure, does not start the voice bridge and does not retry accept inside that delivery.
9. `pytest tests/test_whatsapp_call_control.py tests/test_whatsapp_ingress.py tests/test_jvconnect_whatsapp_client.py -v -m unit` passes without a live call, Meta app, or LiveKit room.
10. No second connector id, no outbound dial, and no customer principal appear in the new modules.

## Open Questions

- Spoken refusal. The approved direction says a refused caller hears a short spoken reason and a hangup. This map says a refused caller never gets an accept. jvvoice disconnect (`api.py`, `connector_client.py`) hangs up through LiveKit and does not play audio. Those cannot all be true on the current jvvoice API. This blocks the spoken-refusal behavior. It does not block the never-accept tests above. Until it is resolved, this spec implements never-accept and does not invent a media session for the refusal.
- Whether disconnect-before-accept actually ends a Meta call that LiveKit has not accepted. The mock can prove Integral's request. A live proof can wait, and a failure must still not fall through to accept.
- How long credential fetch plus jvvoice accept takes against Meta's answer window. jvconnect's forward timeout is 120 seconds (`DEFAULT_AGENT_FORWARD_TIMEOUT_MS`). The Meta window is not in the code read here. Measurement can wait until a staging call. The webhook must not wait for the realtime model.
- The exact Meta `call.event` vocabulary. Handling keys off SDP presence so this does not block mocked tests.
