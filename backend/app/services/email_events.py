"""Verified provider event ingestion, correlation and monotonic delivery state."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.exceptions import BadRequestError
from app.services.email_configuration import unseal
from app.services.email_delivery import database, message_object
from app.utils.time import utc_now_iso

STATES = {
    "queued": 0,
    "dispatching": 0,
    "unknown": 0,
    "accepted": 1,
    "delayed": 2,
    "delivered": 3,
    "failed": 4,
    "bounced": 5,
    "complaint": 6,
}
EVENTS = {
    "email.sent": "accepted",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delayed",
    "email.bounced": "bounced",
    "email.complained": "complaint",
    "email.failed": "failed",
    "email.suppressed": "failed",
    "processed": "accepted",
    "delivered": "delivered",
    "deferred": "delayed",
    "bounce": "bounced",
    "dropped": "failed",
    "spamreport": "complaint",
}


def verify(provider: str, key: str, raw: bytes, headers: dict) -> str:
    """Verify exact raw bytes with provider-specific signing authority."""
    try:
        if provider == "resend":
            event_id = headers["svix-id"]
            timestamp = headers["svix-timestamp"]
            if abs(time.time() - int(timestamp)) > 300:
                raise ValueError()
            secret = base64.b64decode(key.removeprefix("whsec_"), validate=True)
            signed = event_id.encode() + b"." + timestamp.encode() + b"." + raw
            expected = base64.b64encode(
                hmac.new(secret, signed, hashlib.sha256).digest()
            ).decode()
            if not any(
                hmac.compare_digest(part, "v1," + expected)
                for part in headers["svix-signature"].split()
            ):
                raise ValueError()
            return event_id
        if provider != "sendgrid":
            raise ValueError()
        timestamp = headers["x-twilio-email-event-webhook-timestamp"]
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError()
        public = serialization.load_der_public_key(base64.b64decode(key, validate=True))
        if not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError()
        public.verify(
            base64.b64decode(
                headers["x-twilio-email-event-webhook-signature"], validate=True
            ),
            timestamp.encode() + raw,
            ec.ECDSA(hashes.SHA256()),
        )
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        raise BadRequestError("Invalid email webhook signature") from None


async def ingest(connection_id: str, raw: bytes, headers: dict) -> dict:
    """Verify provider signatures before retaining or correlating events."""
    if len(raw) > 1_000_000:
        raise BadRequestError("Email webhook payload is too large")
    connection = await database().get("object", f"o.EmailConnection.{connection_id}")
    if not connection:
        raise BadRequestError("Unknown email webhook connection")
    workspace = connection["context"]["workspace_id"]
    config = unseal(connection["context"]["snapshot"], workspace)
    if not config.webhook_key:
        raise BadRequestError("Email webhook verification is not configured")
    envelope_id = verify(config.provider, config.webhook_key, raw, headers)
    try:
        body = json.loads(raw)
        events = body if config.provider == "sendgrid" else [body]
        if not isinstance(events, list) or len(events) > 1000:
            raise ValueError()
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                raise ValueError()
            state = EVENTS.get(
                event.get("event")
                if config.provider == "sendgrid"
                else event.get("type")
            )
            if not state:
                continue
            data = event if config.provider == "sendgrid" else event.get("data", {})
            ref = str(data.get("sg_message_id") or data.get("email_id") or "")
            # SendGrid appends a per-recipient suffix to its request message ID.
            if config.provider == "sendgrid":
                ref = ref.split(".")[0]
            hint = (
                str(data.get("integral_message_id") or "")
                if config.provider == "sendgrid"
                else ""
            )
            event_id = str(event.get("sg_event_id") or f"{envelope_id}:{index}")
            identity = hashlib.sha256(
                f"{connection_id}:{event_id}".encode()
            ).hexdigest()
            await database().insert_if_absent(
                "object",
                {
                    "id": f"o.EmailProviderEvent.{identity}",
                    "entity": "EmailProviderEvent",
                    "context": {
                        "workspace_id": workspace,
                        "connection_id": connection_id,
                        "provider_message_id": ref,
                        "message_hint": hint,
                        "status": state,
                        "created_at": utc_now_iso(),
                    },
                },
            )
            query = {
                "entity": "EmailMessage",
                "context.workspace_id": workspace,
                "context.connection_id": connection_id,
            }
            if hint:
                query["context.message_id"] = hint
            elif ref:
                query["context.provider_message_id"] = ref
            else:
                continue
            matches = await database().find("object", query, limit=2)
            if len(matches) == 1:
                await reconcile_message(matches[0]["context"]["message_id"])
    except (ValueError, TypeError, AttributeError):
        raise BadRequestError("Invalid email webhook payload") from None
    return {"ok": True}


async def reconcile_message(identity: str) -> None:
    """Apply retained delivery evidence without regressing known terminal outcomes."""
    row = await database().get("object", message_object(identity))
    if not row:
        return
    data = row["context"]
    query = {
        "entity": "EmailProviderEvent",
        "context.workspace_id": data["workspace_id"],
        "context.connection_id": data["connection_id"],
    }
    match = [{"context.message_hint": identity}]
    if data.get("provider_message_id"):
        match.append(
            {
                "context.provider_message_id": (
                    data["provider_message_id"].split(".")[0]
                    if data["provider"] == "sendgrid"
                    else data["provider_message_id"]
                )
            }
        )
    query["$or"] = match
    events = await database().find("object", query, limit=1000)
    state = max(
        [data["status"]] + [e["context"]["status"] for e in events],
        key=lambda s: STATES.get(s, 0),
    )
    # Re-read/CAS prevents a late acceptance from regressing a concurrent bounce.
    if state != data["status"]:
        changed = await database().find_one_and_update(
            "object",
            {"id": row["id"], "context.status": data["status"]},
            {
                "$set": {
                    "context.status": state,
                    "context.updated_at": utc_now_iso(),
                    "context.payload_enc": "",
                }
            },
        )
        if not changed:
            await reconcile_message(identity)
