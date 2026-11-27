# Spec: call-admission

Module id: `call-admission`. Depends on the existing WhatsApp binding. No other module in this initiative.

## Objective

Decide whether one inbound WhatsApp caller may be answered on the workspace line. The only caller who may be answered is the verified personal number for that line. Every other caller is refused. The decision performs no HTTP and does not call Meta, LiveKit, jvconnect, or jvvoice.

The user is the owner who already linked a personal WhatsApp number and attached a workspace to the JV Connect line. Success is a pure, unit-tested answer-or-refuse result that `call-control` can trust before it places any network call.

Text chat already admits a broader set of senders. `receive_whatsapp_webhook` resolves any verified `ChannelIdentity` and then any workspace that sender can use (`backend/app/agentive/services/whatsapp_ingress.py`, `_verified_sender`, `_accessible_workspaces`). Calling does not reuse that breadth.

## Assumptions

1. The verified personal number is not `WhatsAppChannelBinding.display_phone` or `phone_number_id`. Those fields are the business line, filled from jvconnect account data (`backend/app/agentive/services/whatsapp_channel_binding.py`, `save_whatsapp_channel_credentials`). The personal number is a verified `ChannelIdentity` with `channel="whatsapp"` (`backend/app/agentive/nodes.py`, `ChannelIdentity`).
2. A personal number is a verified WhatsApp identity owned by `WhatsAppChannelBinding.user_id`, the user who saved the line. The inbound caller must match exactly one of those identities. Zero matches, or more than one identity with the same number, refuses the call. A workspace member who can text the line, but is not that identity, is refused.
3. The workspace is the single enabled `UsesWhatsAppLine` grant on that binding (`backend/app/agentive/edges.py`). Zero enabled grants, or more than one, refuses. There is no spoken workspace picker on a call. Text chat's "switch" / numeric picker (`whatsapp_ingress.py`) stays on the text path.
4. Calling has its own flag, `calling_enabled`, on `UsesWhatsAppLine`, default `false`. `grant.enabled` and `binding.enabled` remain the text-line switches (`set_workspace_line_enabled` in `whatsapp_channel_binding.py`). Turning calling on does not change `ChannelInstallation` enablement in `backend/app/connectors/bundled_packages/whatsapp/adapter/grants.py`.
5. Both the line and the grant must already be enabled, and `calling_enabled` must be true, before an answer. A disabled line refuses.
6. Phone comparison is digit identity after E.164 normalization. Meta `call.from` may omit `+`. `normalize_whatsapp_e164` (`backend/app/agentive/services/channel_identity.py`) returns empty unless the value already starts with `+`, so the matcher ensures a leading `+` before calling it, then compares the resulting digits. `15551212000` and `+1 555 121 2000` match `+15551212000`. A value that still fails normalization refuses.
7. The decision function receives values the caller already loaded. Graph reads, when a later module needs them, sit outside the pure function. This module's automated bar does not open a database connection.
8. No customer principal and no outbound dial. Catalogue id remains `whatsapp_jvconnect`.

## Tech Stack

- Python 3.11+, the existing Integral backend.
- No new dependency.
- Persistence, when a writer is added, uses the existing `UsesWhatsAppLine` edge. Do not add a `Node` for the decision.
- Errors, if a writer rejects a bad update, use `JVSpatialAPIException` subclasses from `backend/app/api/errors.py` (`BadRequestError`, `ResourceConflictError`). The pure decision function returns a result value and does not raise for a refused caller.

## Commands

From `backend/`:

```bash
uv sync --frozen --extra dev --extra test
pytest tests/test_whatsapp_call_admission.py -v -m unit
black app/agentive/services/whatsapp_call_admission.py tests/test_whatsapp_call_admission.py
isort app/agentive/services/whatsapp_call_admission.py tests/test_whatsapp_call_admission.py
flake8 app/agentive/services/whatsapp_call_admission.py tests/test_whatsapp_call_admission.py
```

`pytest` is already configured with `asyncio_mode = "auto"` and `--strict-markers` (`backend/pyproject.toml`). These tests do not need a database, so `-m unit` matches `backend/tests/test_whatsapp_ingress.py`.

## Project Structure

```
backend/app/agentive/services/whatsapp_call_admission.py
    Pure decision and the in-memory inputs it needs.
backend/app/agentive/edges.py
    UsesWhatsAppLine.calling_enabled, default false, when the flag is persisted.
backend/tests/test_whatsapp_call_admission.py
    Unit tests. No graph, HTTP, or fixtures that boot the app.
docs/ideas/whatsapp-calling/SPEC-call-admission.md
    This spec.
```

`call-control` loads the binding, the owner's verified identities, and the grants, then calls the pure function. This module does not register an HTTP route and does not import `jvconnect_whatsapp` or `native_voice`.

A settings screen for the flag is out of this module. The field defaults false so an existing line does not start answering calls. A later change may set the field through the existing grant writer; it must not reuse `SaveWhatsAppChannelBindingRequest` (`backend/app/schemas/agentive/whatsapp_channel.py`), which is the JV Connect credential body.

## Code Style

Match `whatsapp_ingress.py`: module docstring, `from __future__ import annotations`, frozen dataclasses, small functions, no network inside the decision.

```python
@dataclass(frozen=True)
class CallerAdmission:
    """Answer or refuse one inbound caller. Refuse is not an exception."""

    decision: Literal["answer", "refuse"]
    reason: str
    verified_e164: str = ""
    workspace_id: str = ""
    principal_id: str = ""


def admit_whatsapp_caller(
    caller: str,
    *,
    verified_e164: str,
    line_enabled: bool,
    calling_enabled: bool,
) -> CallerAdmission:
    """Match the caller to one verified personal number. No I/O."""
```

Reason codes are stable strings tests assert on: `answer`, `empty_caller`, `invalid_caller`, `no_verified_number`, `ambiguous_verified_number`, `caller_mismatch`, `line_disabled`, `calling_disabled`, `no_workspace`, `ambiguous_workspace`. Workspace and identity cardinality are inputs (`verified_e164` empty vs an explicit ambiguous flag, and workspace id empty vs an explicit ambiguous flag), so the pure function still does no lookup.

Naming: `admit_whatsapp_caller`, not a new connector id. Format with black and isort. New public functions get type annotations.

## Testing Strategy

Framework: pytest, marker `unit`, same style as `backend/tests/test_whatsapp_ingress.py` (plain asserts, no live server). Location: `backend/tests/test_whatsapp_call_admission.py`.

These tests construct strings and booleans only. They do not import httpx, open a socket, or call `ChannelIdentity.find`.

Required cases:

- Exact digit match with both flags true returns `answer` and echoes the normalized E.164, workspace id, and principal id that were passed in.
- The same number with or without `+`, spaces, or punctuation matches.
- A different number, an empty caller, and a too-short or too-long digit string refuse with distinct reasons.
- Empty verified number and the ambiguous-identity input refuse with `no_verified_number` and `ambiguous_verified_number`.
- `line_enabled` false refuses even when the number matches. `calling_enabled` false refuses even when the line is enabled and the number matches.
- Missing workspace and ambiguous workspace refuse with `no_workspace` and `ambiguous_workspace`.
- The business `display_phone` is not an input. A test that only has the business number, and an empty verified personal number, refuses.

No coverage gate beyond these cases. A refused result is success for the function, not a test failure.

## Boundaries

- Always: keep the decision pure; default `calling_enabled` to false; compare only the binding owner's single verified personal number; return a reason code; leave text ingress behavior unchanged.
- Ask first: adding `calling_enabled` to `UsesWhatsAppLine`, or any writer that sets it, because that is a stored edge field. Changing `grant.enabled` semantics. Admitting any principal other than that one identity.
- Never: call Meta, LiveKit, jvconnect, jvvoice, or any other HTTP client from this module. Never treat `display_phone` or `phone_number_id` as the personal number. Never add a connector id. Never introduce a customer principal. Never place an outbound call. Never treat a spoken "yes" as input to this decision. Never log a full phone number at info level; tests may pass numbers as arguments.

## Success Criteria

1. `admit_whatsapp_caller` returns `answer` only when the caller's digits equal the single verified personal E.164 and both `line_enabled` and `calling_enabled` are true.
2. `15551212000`, `+15551212000`, and `+1 555 121 2000` are the same number under `normalize_whatsapp_e164` after a leading `+` is ensured. A non-normalizable value refuses with `invalid_caller`.
3. Mismatch, empty caller, empty verified number, ambiguous verified number, disabled line, and disabled calling each return `refuse` with their own reason code.
4. The business line's `display_phone` and `phone_number_id` cannot produce `answer`.
5. Zero or several enabled workspace grants refuse. The answer result carries at most one `workspace_id`.
6. `calling_enabled` defaults false and is not `UsesWhatsAppLine.enabled`. Text enablement in `grants.py` `observe()` stays `grant.enabled and binding.enabled`.
7. `pytest tests/test_whatsapp_call_admission.py -v -m unit` passes with no network and no database.
8. The module imports neither `jvconnect_whatsapp` nor `native_voice`.
9. No new catalogue connector and no connector id other than the existing `whatsapp` / `whatsapp_jvconnect` line.
10. No code path in this module accepts a second caller, a customer principal, or an outbound dial.

## Open Questions

- How an operator turns `calling_enabled` on without a new settings screen. The flag's default and meaning are specified. The writer can wait as long as tests pass the boolean in directly. Not a blocker for the pure decision.
- Whether a later customer-admission rule will replace "binding owner's single identity" with another principal. Out of this module. Not a blocker.
- Spoken refusal text. This module only returns `refuse`. Whether the caller hears speech is `call-control`'s open question.
