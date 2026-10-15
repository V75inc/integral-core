"""Stable fingerprints for generated documents (template + merge inputs)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def stable_document_source_fingerprint(payload: Dict[str, Any]) -> str:
    """SHA-256 hex digest of a JSON-serializable payload (sorted keys)."""
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
