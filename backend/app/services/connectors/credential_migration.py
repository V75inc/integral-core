"""Rewrite legacy sync connector credentials in a stopped deployment."""

from app.agentive.nodes import Connector
from app.agentive.services.connector_registry_node import (
    decrypt_auth_state,
    encode_package_secrets,
    encrypt_auth_state,
)
from app.services.credential_crypto import encryption_available


async def migrate(*, reverse: bool, dry_run: bool) -> int:
    """Rewrite existing credential fields and return the changed row count."""
    if not encryption_available():
        raise RuntimeError("Connector credential migration requires the storage key")
    changed = 0
    for connector in await Connector.find():
        current = dict(connector.auth_state or {})
        protected = {
            field["key"]
            for field in (connector.package_manifest or {}).get("configuration", [])
            if field.get("type") == "secret"
        }
        decoded = decrypt_auth_state(current, strict=True)
        if reverse:
            updated = decoded
        else:
            updated = encrypt_auth_state(encode_package_secrets(current, protected))
        if current == updated:
            continue
        if not dry_run:
            connector.auth_state = updated
            await connector.save()
        changed += 1
    return changed
