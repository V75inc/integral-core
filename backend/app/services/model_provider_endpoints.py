"""Canonical endpoints for workspace model choices, independent of host env."""

from urllib.parse import urlsplit, urlunsplit

OLLAMA_CLOUD_API_BASE = "https://ollama.com"
OLLAMA_LOCAL_DEFAULT_API_BASE = "http://localhost:11434"


# Explicit SDK-only sentinel prevents fallback to deployment provider keys.
# The guarded custom-model transport removes this Authorization header before
# sending a genuinely keyless request to the selected endpoint.
KEYLESS_PLACEHOLDER_API_KEY = "integral-keyless-endpoint"


def normalize_custom_base_url(base_url: str | None) -> str:
    """Canonical nonsecret endpoint identity, shared by schemas and routing."""
    raw = (base_url or "").strip()
    if not raw or len(raw) > 512 or any(c.isspace() or ord(c) < 32 for c in raw):
        raise ValueError("base_url must be a nonempty URL of at most 512 characters")
    if "\\" in raw:
        raise ValueError("base_url must not contain backslashes")
    try:
        parsed = urlsplit(raw)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme not in {"https", "http"} or not host:
            raise ValueError("base_url must include an http/https host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        host = host.encode("idna").decode("ascii").lower()
    except ValueError as exc:
        raise ValueError(
            "base_url must be a valid URL without credentials, query or fragment"
        ) from exc
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
