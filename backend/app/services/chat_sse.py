"""Dependency-free encoding for public chat SSE frames."""

import json
from typing import Any, Dict


def sse_bytes(event: str, data: Dict[str, Any]) -> bytes:
    """Encode a named SSE event frame with JSON-serialized payload."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")
