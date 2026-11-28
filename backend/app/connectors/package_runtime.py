"""Execute declarative connector requests. Secrets are injected, never templated."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.services.url_safety import (
    guarded_async_client,
    pin_public_dns,
    validate_outbound_http_url,
)


def _http(
    client: httpx.AsyncClient | None, timeout: float
) -> tuple[httpx.AsyncClient, bool]:
    """Use the caller's client, or a guarded client that checks redirects."""
    if client is not None:
        return client, False
    return guarded_async_client(timeout=timeout, follow_redirects=True), True


_ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_TEMPLATE = re.compile(r"\{\{\s*([a-z]+)(?:\.([A-Za-z0-9_]+))?\s*\}\}")
_SELECT = re.compile(r"^\$(?:\.([A-Za-z_][A-Za-z0-9_]*))*(\[\*\])?$")


def render(
    value: Any, config: dict[str, Any], inputs: dict[str, Any] | None = None
) -> Any:
    """Substitute {{config.*}} and {{input.*}}. {{secret.*}} is refused."""
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            scope, key = match.group(1), match.group(2)
            if scope == "secret":
                raise ValueError("secrets cannot be templated")
            if scope == "cursor":
                found = (inputs or {}).get("cursor", "")
            elif scope == "config":
                found = config.get(key or "", "")
            elif scope == "input":
                found = (inputs or {}).get(key or "", "")
            else:
                raise ValueError(f"unknown template scope {scope}")
            return "" if found is None else str(found)

        return _TEMPLATE.sub(replace, value)
    if isinstance(value, dict):
        return {key: render(item, config, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, config, inputs) for item in value]
    return value


def select_output(body: Any, path: str | None) -> Any:
    """Apply a bounded JSON path: `$`, `$.field`, or `$.field[*]`."""
    if not path or path == "$":
        return body
    if not _SELECT.match(path):
        raise ValueError("output select is outside the supported path grammar")
    parts = [part for part in path[2:].replace("[*]", "").split(".") if part]
    current = body
    for part in parts:
        if isinstance(current, list):
            current = [
                item.get(part) if isinstance(item, dict) else None for item in current
            ]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def request_url(
    manifest: dict[str, Any],
    request: dict[str, Any],
    config: dict[str, Any],
    inputs: dict[str, Any],
) -> str:
    """Join the configured base URL with a relative path."""
    base = str(render(manifest["http"]["base_url"], config, inputs)).rstrip("/")
    path = str(render(request["path"], config, inputs))
    if not _relative_path(path):
        raise ValueError("request path must be relative")
    return base + path


def _relative_path(path: str) -> bool:
    """True when the path stays under the base URL."""
    lowered = path.lower()
    if (
        not path.startswith("/")
        or "://" in path
        or "\\" in path
        or "//" in path
        or "%2e%2e" in lowered
    ):
        return False
    return all(part not in {".", ".."} for part in path.split("/"))


def webhook_signature_ok(body: bytes, header: str, secret: str) -> bool:
    """True when the header is the HMAC-SHA256 hex digest of the raw body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    presented = header.strip()
    if presented.lower().startswith("sha256="):
        presented = presented.split("=", 1)[1].strip()
    if not presented:
        return False
    return hmac.compare_digest(digest, presented)


def oauth_consent_url(
    auth: dict[str, Any], *, client_id: str, redirect_uri: str, state: str
) -> str:
    """Build a provider consent URL from the package OAuth block."""
    params = {
        key: str(value)
        for key, value in (auth.get("extra_authorize_params") or {}).items()
    }
    params.update(
        client_id=client_id,
        scope=" ".join(auth.get("scopes") or []),
        redirect_uri=redirect_uri,
        response_type="code",
        state=state,
    )
    return str(httpx.URL(auth["authorize_url"]).copy_merge_params(params))


def consent_url_for_package(
    package_id: str,
    *,
    stored_client_id: str | None,
    redirect_uri: str | None,
    state: str,
) -> str | None:
    """Build a re-authorize URL from the admitted package, or None if it is absent."""
    auth = admitted_oauth(package_id)
    if not auth:
        return None
    client = auth.get("client") if isinstance(auth.get("client"), dict) else {}
    client_id = (stored_client_id or "").strip() or deployment_setting(
        list(client.get("id_env") or [])
    )
    if not client_id:
        missing = (list(client.get("id_env") or ["client id"]))[0]
        raise ValueError(str(missing))
    effective_redirect = redirect_uri or deployment_setting(
        list(auth.get("redirect_env") or [])
    )
    return oauth_consent_url(
        auth,
        client_id=client_id,
        redirect_uri=effective_redirect,
        state=state,
    )


def deployment_setting(names: list[str]) -> str:
    """Return the first non-empty deployment setting or environment variable."""
    import os

    from app.config import settings

    for name in names:
        value = str(getattr(settings, name, "") or os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def admitted_manifest(package_id: str) -> dict[str, Any] | None:
    """Return one admitted package, scanning first-party packages once if needed."""
    from app.connectors.package_discovery import current_admission, rescan

    admission = current_admission()
    if (
        package_id not in admission.packages
        and not admission.packages
        and not admission.rejected
    ):
        admission = rescan()
    return admission.packages.get(package_id)


def package_record_type(manifest: dict[str, Any] | None, package_id: str) -> str:
    """Return the record entry type from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "record_type" not in sync:
            return None
        raw = sync.get("record_type")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a record type")
    return found


def package_child_type(manifest: dict[str, Any] | None, package_id: str) -> str:
    """Return the child entry type from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "child_type" not in sync:
            return None
        raw = sync.get("child_type")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a child type")
    return found


def package_entity_type(
    manifest: dict[str, Any] | None, package_id: str, entity: str
) -> str:
    """Return one entity's entry type from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "entity_types" not in sync:
            return None
        mapping = sync.get("entity_types")
        if not isinstance(mapping, dict) or entity not in mapping:
            return None
        raw = mapping.get(entity)
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare an entity type")
    return found


_CONFLICT_POLICIES = {"last_write_wins", "manual_resolve", "mirror_only"}


def package_conflict_policy(manifest: dict[str, Any] | None, package_id: str) -> str:
    """Return the conflict policy from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "conflict_policy" not in sync:
            return None
        raw = sync.get("conflict_policy")
        if raw not in _CONFLICT_POLICIES:
            raise ValueError("package does not declare a conflict policy")
        return str(raw)

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a conflict policy")
    return found


def package_title_prefix(manifest: dict[str, Any] | None, package_id: str) -> str:
    """Return the empty-title prefix from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "title_prefix" not in sync:
            return None
        raw = sync.get("title_prefix")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a title prefix")
    return found


def package_title_prefix_for(
    manifest: dict[str, Any] | None, package_id: str, entity: str
) -> str:
    """Return one entity's title prefix from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "title_prefixes" not in sync:
            return None
        mapping = sync.get("title_prefixes")
        if not isinstance(mapping, dict) or entity not in mapping:
            return None
        raw = mapping.get(entity)
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a title prefix")
    return found


def package_field_name(
    manifest: dict[str, Any] | None, package_id: str, field: str
) -> str:
    """Return one destination field name from the snapshot, or the admitted package."""

    def declared(source: dict[str, Any] | None) -> str | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "field_names" not in sync:
            return None
        mapping = sync.get("field_names")
        if not isinstance(mapping, dict) or field not in mapping:
            return None
        raw = mapping.get(field)
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()

    found = declared(manifest)
    if found is None:
        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a field name")
    return found


def package_host_managed(
    manifest: dict[str, Any], package_id: str | None = None
) -> bool:
    """Return whether the host already keeps this package. Omitted means no."""

    def declared(source: Any) -> bool | None:
        if not isinstance(source, dict) or "host_managed" not in source:
            return None
        value = source.get("host_managed")
        if not isinstance(value, bool):
            raise ValueError("package does not declare host management")
        return value

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    return bool(found)


_ADAPTER_REF = re.compile(
    r"^adapter(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


def package_description(manifest: dict[str, Any], package_id: str | None = None) -> str:
    """Return the library-card description. A stored description wins."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "description" not in source:
            return None
        raw = source.get("description")
        if not isinstance(raw, str) or not raw or len(raw) > 400:
            raise ValueError("package does not declare a description")
        return raw

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a description")
    return found


def package_name(manifest: dict[str, Any], package_id: str | None = None) -> str:
    """Return the library-card name. A stored name wins."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "name" not in source:
            return None
        raw = source.get("name")
        if not isinstance(raw, str) or not raw or len(raw) > 80:
            raise ValueError("package does not declare a name")
        return raw

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a name")
    return found


_PACKAGE_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$"
)


def package_version(manifest: dict[str, Any], package_id: str | None = None) -> str:
    """Return the library-card version. A stored version wins."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "version" not in source:
            return None
        raw = source.get("version")
        if not isinstance(raw, str) or _PACKAGE_VERSION.fullmatch(raw) is None:
            raise ValueError("package does not declare a version")
        return raw

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a version")
    return found


_PUBLISHER = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


def package_publisher(manifest: dict[str, Any], package_id: str | None = None) -> str:
    """Return the library-card publisher. A stored publisher wins."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "publisher" not in source:
            return None
        raw = source.get("publisher")
        if not isinstance(raw, str) or _PUBLISHER.fullmatch(raw) is None:
            raise ValueError("package does not declare a publisher")
        return raw

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a publisher")
    return found


_PACKAGE_ICON = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_./-]+$")


def package_icon(manifest: dict[str, Any], package_id: str | None = None) -> str | None:
    """Return the library-card icon path. An omitted icon stays omitted."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "icon" not in source:
            return None
        raw = source.get("icon")
        if not isinstance(raw, str) or _PACKAGE_ICON.fullmatch(raw) is None:
            raise ValueError("package does not declare an icon")
        return raw

    if isinstance(manifest, dict) and "icon" in manifest:
        return declared(manifest)
    if package_id:
        admitted = admitted_manifest(package_id) or {}
        if "icon" in admitted:
            return declared(admitted)
    return None


_CAPABILITY_KINDS = frozenset({"actions", "mcp", "sync", "channel"})


_PACKAGE_SCHEMA = "integral.connector/v1"


def package_schema(manifest: dict[str, Any], package_id: str | None = None) -> str:
    """Return the manifest schema. A stored schema wins."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "schema" not in source:
            return None
        raw = source.get("schema")
        if raw != _PACKAGE_SCHEMA:
            raise ValueError("package does not declare a schema")
        return _PACKAGE_SCHEMA

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a schema")
    return found


def package_capabilities(
    manifest: dict[str, Any], package_id: str | None = None
) -> list[str]:
    """Return the declared capability kinds. A stored block wins."""

    def declared(source: Any) -> list[str] | None:
        if not isinstance(source, dict) or "capabilities" not in source:
            return None
        caps = source.get("capabilities")
        if (
            not isinstance(caps, dict)
            or not caps
            or any(key not in _CAPABILITY_KINDS for key in caps)
        ):
            raise ValueError("package does not declare a capability")
        return sorted(caps)

    found = declared(manifest)
    if found is None and package_id:
        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare a capability")
    return found


def package_core(manifest: dict[str, Any], package_id: str | None = None) -> str | None:
    """Return a compatible Core range. An omitted range stays omitted."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict) or "core" not in source:
            return None
        raw = source.get("core")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("package does not declare a core range")
        return raw.strip()

    found = (
        declared(manifest)
        if isinstance(manifest, dict) and "core" in manifest
        else None
    )
    if found is None and package_id:
        admitted = admitted_manifest(package_id) or {}
        if "core" in admitted:
            found = declared(admitted)
    if found is None:
        return None
    from app.connectors.package_discovery import _core_requirement

    if _core_requirement(found):
        raise ValueError("package does not declare a core range")
    return found


def package_requires_mcp_oauth(package_id: str) -> bool:
    """True when the admitted package declares MCP authorization."""
    manifest = admitted_manifest(package_id) or {}
    return (manifest.get("auth") or {}).get("type") == "mcp_oauth"


_SNAPSHOT_KEYS = (
    "id",
    "version",
    "http",
    "capabilities",
    "auth",
    "health",
    "configuration",
)


def capture_package_snapshot(
    connector: Any, package_id: str, *, replace: bool = True
) -> None:
    """Store the admitted package on a catalog connector without changing its class.

    ``replace=False`` keeps a snapshot that already has an ``http`` block, so a
    later OAuth completion does not move tokens, the cursor, or the stored host.
    """
    import copy

    from app.connectors.package_discovery import current_admission

    stored = getattr(connector, "package_manifest", None)
    if (
        not replace
        and isinstance(stored, dict)
        and isinstance(stored.get("http"), dict)
    ):
        return
    manifest = admitted_manifest(package_id)
    if not manifest:
        return
    connector.package_manifest = copy.deepcopy(
        {key: manifest[key] for key in _SNAPSHOT_KEYS if key in manifest}
    )
    connector.package_version = str(manifest.get("version") or "")
    connector.package_fingerprint = current_admission().fingerprints.get(package_id, "")


def package_http(
    package_id: str, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return the snapshot host block, filling named hosts from the package."""
    admitted = admitted_manifest(package_id) or {}
    admitted_http = admitted.get("http")
    if not isinstance(admitted_http, dict):
        admitted_http = {}
    if isinstance(manifest, dict) and isinstance(manifest.get("http"), dict):
        http = dict(manifest["http"])
    else:
        http = dict(admitted_http)
    if "hosts" not in http and "hosts" in admitted_http:
        http["hosts"] = admitted_http["hosts"]
    if "hosts" in http:
        hosts = http.get("hosts")
        if not isinstance(hosts, dict) or not hosts:
            raise ValueError("package does not declare named hosts")
        for name, url in hosts.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(url, str)
                or not url.startswith("https://")
            ):
                raise ValueError("package does not declare named hosts")
    return http


def package_action_request(
    package_id: str,
    action_name: str,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return one action request from the snapshot, or the admitted package."""

    def _find(source: dict[str, Any]) -> dict[str, Any] | None:
        actions = (source.get("capabilities") or {}).get("actions") or []
        for action in actions:
            if not isinstance(action, dict):
                continue
            request = action.get("request")
            if action.get("name") == action_name and isinstance(request, dict):
                return request
        return None

    if isinstance(manifest, dict):
        found = _find(manifest)
        if found:
            return found
    return _find(admitted_manifest(package_id) or {})


def package_action_url(
    package_id: str,
    action_name: str,
    config: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> str | None:
    """Return one declared action URL, or None when the package has no such action.

    A stored installation snapshot wins over the currently admitted package.
    """
    stored = (
        manifest
        if isinstance(manifest, dict) and manifest.get("capabilities")
        else None
    )
    source = stored or admitted_manifest(package_id) or {}
    for action in (source.get("capabilities") or {}).get("actions") or []:
        if action.get("name") == action_name and action.get("request"):
            return request_url(source, action["request"], config or {}, {})
    return None


def admitted_oauth(package_id: str) -> dict[str, Any] | None:
    """Return the admitted oauth2 block, scanning first-party packages once if needed."""
    auth = (admitted_manifest(package_id) or {}).get("auth") or {}
    if auth.get("type") == "oauth2" and auth.get("token_url"):
        return auth
    return None


def mcp_mount_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Prefer the admitted package's MCP mount. The catalog fills any gap."""
    manifest = admitted_manifest(str(entry.get("slug") or "")) or {}
    mcp = (manifest.get("capabilities") or {}).get("mcp") or {}
    command = [str(part) for part in (mcp.get("command") or [])]
    catalog_args = [str(arg) for arg in (entry.get("args") or [])]
    return {
        "transport": mcp.get("transport")
        or entry.get("transport")
        or "streamable_http",
        "url": mcp.get("url") or entry.get("url") or "",
        "command": command[0] if command else entry.get("command"),
        "args": command[1:] if command else catalog_args,
    }


def stdio_oauth_package(package_id: str) -> dict[str, Any] | None:
    """Return a package whose OAuth consent starts a local MCP server."""
    manifest = admitted_manifest(package_id) or {}
    auth = manifest.get("auth") or {}
    mcp = (manifest.get("capabilities") or {}).get("mcp") or {}
    if auth.get("type") == "oauth2" and mcp.get("transport") == "stdio":
        return manifest
    return None


async def post_oauth_token(
    auth: dict[str, Any],
    body: dict[str, str],
    *,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """Post a token request to the package token URL. The secret is not logged."""
    token_url = str(auth.get("token_url") or "")
    if not token_url.startswith("https://"):
        raise ValueError("OAuth token URL must be https")
    await validate_outbound_http_url(token_url)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = dict(body)
    if auth.get("client_authentication") == "client_secret_basic":
        raw = f"{client_id}:{client_secret}".encode("ascii")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    else:
        payload["client_id"] = client_id
        payload["client_secret"] = client_secret
    http, owns = _http(client, 15)
    try:
        with pin_public_dns(token_url):
            response = await http.post(
                token_url,
                headers=headers,
                content=urlencode(payload),
                follow_redirects=False,
            )
        _ensure_response_size(response)
        return response
    finally:
        if owns:
            await http.aclose()


def _request_auth(
    manifest: dict[str, Any], secrets: dict[str, Any]
) -> tuple[dict[str, str], str]:
    """Resolve an injected header and the value to suppress in responses."""
    import os

    auth = manifest.get("auth") or {}
    kind = auth.get("type")
    if kind == "oauth2":
        value = str(secrets.get("access_token") or "")
        if value:
            return {"Authorization": f"Bearer {value}"}, value
        return {}, ""
    inject = auth.get("inject") or {}
    if inject.get("in") != "header":
        return {}, ""
    if kind == "operator":
        value = os.environ.get(auth["env"], "")
    elif kind == "api_key":
        value = str(secrets.get(auth["secret"]) or "")
    else:
        return {}, ""
    if not value:
        return {}, ""
    return {inject["name"]: f"{inject.get('prefix') or ''}{value}"}, value


def _redact_injected_value(data: Any, secret: str) -> Any:
    if isinstance(data, str):
        return data.replace(secret, "[redacted]")
    if isinstance(data, list):
        return [_redact_injected_value(item, secret) for item in data]
    if isinstance(data, dict):
        return {
            key.replace(secret, "[redacted]"): _redact_injected_value(value, secret)
            for key, value in data.items()
        }
    return data


def require_package_url(key: str, value: Any, egress: str) -> None:
    """Accept an https URL. A trusted host must be on the operator list."""
    from urllib.parse import urlparse

    from app.api.errors import BadRequestError
    from app.services.url_safety import outbound_trusted_hosts

    if not isinstance(value, str):
        raise BadRequestError(f"{key} must be an https URL")
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme != "https" or not host:
        raise BadRequestError(f"{key} must be an https URL")
    if egress == "trusted_host" and host not in outbound_trusted_hosts():
        raise BadRequestError(f"{key} requires a trusted host")


async def perform_request(
    manifest: dict[str, Any],
    request: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any],
    inputs: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
    with_headers: bool = False,
    ok_statuses: list[int] | None = None,
) -> Any:
    """Validate the host, send one declarative request, and return JSON."""
    inputs = inputs or {}
    for field in manifest.get("configuration") or []:
        if (
            isinstance(field, dict)
            and field.get("type") == "url"
            and field.get("egress") == "trusted_host"
            and config.get(field.get("key")) not in (None, "")
        ):
            require_package_url(
                str(field.get("key") or "url"),
                config[field["key"]],
                "trusted_host",
            )
    url = request_url(manifest, request, config, inputs)
    await validate_outbound_http_url(url)
    headers = {
        key: str(render(value, config, inputs))
        for key, value in (request.get("headers") or {}).items()
    }
    injected, injected_value = _request_auth(manifest, secrets)
    headers.update(injected)
    query = {
        key: str(render(value, config, inputs))
        for key, value in (request.get("query") or {}).items()
    }
    body = (
        render(request.get("body"), config, inputs)
        if request.get("body") is not None
        else None
    )
    timeout = _request_timeout(manifest)
    http, owns = _http(client, timeout)
    try:
        with pin_public_dns(url):
            response = await http.request(
                request["method"],
                url,
                params=query,
                headers=headers,
                json=body,
                follow_redirects=False if injected else httpx.USE_CLIENT_DEFAULT,
            )
    finally:
        if owns:
            await http.aclose()
    if ok_statuses is None:
        response.raise_for_status()
    elif response.status_code not in ok_statuses:
        raise ValueError("health status was not accepted")
    _ensure_response_size(response, manifest)
    if not response.content:
        body = None
    else:
        body = response.json()
    response_headers = dict(response.headers)
    if injected_value:
        body = _redact_injected_value(body, injected_value)
        response_headers = _redact_injected_value(response_headers, injected_value)
    if with_headers:
        return body, response_headers
    return body


async def follow_package_link(
    package_id: str,
    manifest: dict[str, Any] | None,
    url: str,
    headers: dict[str, str],
) -> tuple[Any, dict[str, str]]:
    """GET one absolute link on the package host through the guarded client."""
    http = package_http(package_id, manifest)
    base = str(http.get("base_url") or "").strip()
    if not base:
        raise ValueError("package does not declare a host")
    allowed = urlsplit(base).hostname
    target = urlsplit(url).hostname
    if not allowed or not target or target.lower() != allowed.lower():
        raise ValueError("package link host is not the package host")
    await validate_outbound_http_url(url)
    client = guarded_async_client(
        timeout=_request_timeout({"http": http}), follow_redirects=True
    )
    try:
        with pin_public_dns(url):
            response = await client.request("GET", url, headers=headers)
    finally:
        await client.aclose()
    response.raise_for_status()
    limit_manifest = manifest if isinstance(manifest, dict) else {"http": http}
    _ensure_response_size(response, limit_manifest)
    if not response.content:
        body: Any = None
    else:
        body = response.json()
    return body, {str(key): value for key, value in response.headers.items()}


def _request_timeout(manifest: dict[str, Any]) -> float:
    """Return the request timeout, capped at 30 seconds."""
    raw = (manifest.get("http") or {}).get("timeout_seconds", 10)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 10.0
    if value <= 0:
        return 10.0
    return min(value, 30.0)


def _ensure_response_size(
    response: httpx.Response, manifest: dict[str, Any] | None = None
) -> None:
    """Refuse a body larger than the package limit before it is parsed."""
    if len(response.content) > _response_limit(manifest or {}):
        raise ValueError("response exceeds max_response_bytes")


def _response_limit(manifest: dict[str, Any]) -> int:
    """Return the response size limit. The default is 262144 and the cap is 1048576."""
    raw = (manifest.get("http") or {}).get("max_response_bytes", 262144)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 262144
    return min(max(raw, 1024), 1048576)


def _mapped_row(item: Any, mapping: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: select_output(item, path)
        for key, path in (mapping.get("fields") or {}).items()
    }
    return {
        "external_id": select_output(item, mapping["external_id"]),
        "title": select_output(item, mapping["title"]),
        "body": select_output(item, mapping.get("body")),
        "updated_at": select_output(item, mapping.get("updated_at")),
        "fields": fields,
    }


def page_limit(paginate: dict[str, Any]) -> int:
    """Return how many sync pages to pull. The default is 50 and the cap is 1000."""
    if not paginate:
        return 1
    raw = paginate.get("max_pages", 50)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 50
    return min(max(raw, 1), 1000)


async def pull_declared_sync(
    manifest: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any],
    start_cursor: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Page a declarative sync source and map each item. No Entry is written here."""
    sync = manifest["capabilities"]["sync"]
    source = sync["source"]
    mapping = sync["map"]
    paginate = source.get("paginate") or {}
    max_pages = page_limit(paginate)
    cursor = start_cursor or ""
    rows: list[dict[str, Any]] = []
    for _page in range(max_pages):
        body = await perform_request(
            manifest,
            source["request"],
            config,
            secrets,
            {"cursor": cursor},
        )
        items = select_output(body, source.get("items")) or []
        if not isinstance(items, list):
            break
        rows.extend(_mapped_row(item, mapping) for item in items)
        if not paginate:
            break
        nxt = select_output(body, paginate.get("next_cursor"))
        if not nxt:
            break
        cursor = str(nxt)
    return rows, cursor


class DeclarativePackageSync:
    """Project declarative rows into the sync runtime's entry shape."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        sync = manifest["capabilities"]["sync"]
        self.slug = str(manifest.get("id") or "")
        self.manifest = manifest
        policy = sync.get("conflict_policy") or "mirror_only"
        self.conflict_policy = (
            policy
            if policy in {"last_write_wins", "manual_resolve", "mirror_only"}
            else "mirror_only"
        )

    async def sync_pull(self, *, connector: Any):
        """Yield mapped records and leave the next cursor on the connector."""
        from app.agentive.services.connector_registry_node import decrypt_auth_state
        from app.services.connectors.base import ExternalRecord

        config = getattr(connector, "package_config", None) or {}
        start_cursor = str(getattr(connector, "sync_cursor", None) or "")
        secrets = decrypt_auth_state(getattr(connector, "auth_state", None) or {})
        rows, cursor = await pull_declared_sync(
            self.manifest,
            config,
            secrets,
            start_cursor=start_cursor,
        )
        if cursor:
            connector.sync_cursor = cursor
        for row in rows:
            external_id = row.get("external_id")
            if external_id in (None, ""):
                continue
            updated = row.get("updated_at")
            yield ExternalRecord(
                external_id=str(external_id),
                payload=row,
                updated_at=str(updated) if updated else None,
            )

    def idempotency_key_for(self, record: Any) -> str:
        """Separate records from two packages that share an external id."""
        import hashlib

        return hashlib.sha256(f"{self.slug}:{record.external_id}".encode()).hexdigest()

    def to_entry(self, record: Any):
        """Map one pulled row onto the package entry type."""
        from app.services.connectors.base import MaterializedEntry

        row = record.payload
        sync = self.manifest["capabilities"]["sync"]
        return MaterializedEntry(
            title=str(row.get("title") or record.external_id),
            body=str(row.get("body") or ""),
            entry_type_key=str(sync["entry_type"]),
            custom_fields={
                key: value
                for key, value in dict(row.get("fields") or {}).items()
                if value is not None
            },
            external_updated_at=record.updated_at,
        )


def package_sync_adapter(
    manifest: dict[str, Any], package_id: str
) -> DeclarativePackageSync | None:
    """Return a Core adapter for declarative pulls."""
    sync = (manifest.get("capabilities") or {}).get("sync") or {}
    if not sync:
        return None
    declarative = "source" in sync and "map" in sync
    if declarative:
        return DeclarativePackageSync(
            {**manifest, "id": manifest.get("id") or package_id}
        )
    return None


def declarative_sync_adapter(package_id: str) -> DeclarativePackageSync | None:
    """Resolve the adapter for an admitted package."""
    return package_sync_adapter(admitted_manifest(package_id) or {}, package_id)


async def call_mcp_tool(
    mcp: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    client: httpx.AsyncClient | None = None,
    config: dict[str, Any] | None = None,
) -> Any:
    """Call one tool on a streamable HTTP MCP server after the host check."""
    url = str(render(mcp.get("url") or "", config or {}, {}))
    await validate_outbound_http_url(url)
    http, owns = _http(client, 10)
    try:
        with pin_public_dns(url):
            response = await http.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
            )
    finally:
        if owns:
            await http.aclose()
    response.raise_for_status()
    _ensure_response_size(response)
    return response.json()
