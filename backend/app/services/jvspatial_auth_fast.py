"""Compile each route pattern once in jvspatial's endpoint auth resolver.

``EndpointAuthResolver`` decides, for every request, whether the endpoint needs auth. It
walks every registered endpoint and calls ``_path_matches(pattern, path)`` for each, and
``_path_matches`` rebuilt the pattern's regex (``re.escape`` per segment, string joins,
``re.match`` on a fresh string) on every call. Core registers hundreds of endpoints and the
resolver runs several loops per request, so this was about a third of the CPU of a trivial
authenticated request (profiled: ``/api/users/me/scope`` 38 ms against 9 ms unauthenticated).

The regex for a pattern never changes, so it is built once. Matching semantics are
identical to the library's: same segment handling, same ``{name}``, ``{name:int}`` and
``{name:path}`` rules, same ``re.match`` against ``^/...$``.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, List, Optional

_installed = False


@lru_cache(maxsize=8192)
def _compiled(pattern: str) -> Optional["re.Pattern[str]"]:
    segments = [s for s in pattern.split("/") if s]
    if not segments:
        return None
    regex_parts: List[str] = []
    for seg in segments:
        if seg.startswith("{") and seg.endswith("}"):
            inner = seg[1:-1]
            if ":" in inner:
                _, conv = inner.split(":", 1)
                regex_parts.append("(.+)" if conv == "path" else r"[^/]+")
            else:
                regex_parts.append(r"[^/]+")
        else:
            regex_parts.append(re.escape(seg))
    return re.compile("^/" + "/".join(regex_parts) + "$")


def path_matches_cached(pattern: str, path: str) -> bool:
    if pattern == path:
        return True
    rx = _compiled(pattern)
    if rx is None:
        return path == "/"
    return rx.match(path) is not None


def endpoint_info_matches_method_fast(
    endpoint_info: Any, request_method: Optional[str]
) -> bool:
    """Same answer as the library's, without building a set of methods per endpoint.

    The library builds ``{m.upper() for m in methods}`` for every registered endpoint on every
    request. Endpoints have one to a few methods, so comparing directly is equivalent and
    much cheaper.
    """
    if not request_method:
        return True
    methods = getattr(endpoint_info, "methods", None) or []
    if not methods:
        return True
    upper = request_method.upper()
    for m in methods:
        if m == upper or m.upper() == upper:
            return True
    return False


def install() -> bool:
    """Replace ``_path_matches`` in the resolver module (idempotent)."""
    global _installed
    if _installed:
        return False
    try:
        from jvspatial.api.components import endpoint_auth_resolver as mod
    except Exception:  # pragma: no cover - library layout changed; keep the original
        return False
    mod._path_matches = path_matches_cached  # type: ignore[attr-defined]
    mod._endpoint_info_matches_method = endpoint_info_matches_method_fast  # type: ignore[attr-defined]
    _installed = True
    return True
