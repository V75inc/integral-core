"""Declarative selection and versioned, bounded passage composition.

Definitions select canonical content, never its audience. No remote embedding
provider is enabled implicitly. A profile change creates a separate vector
space; existing vectors are never queried using another model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator


def digest(value: Any) -> str:
    """Hash a canonical JSON representation."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


class IndexDefinition(BaseModel):
    """Track declaration; EntryType form_schema.search_index can narrow it."""

    enabled: bool = True
    fields: list[str] = Field(
        default_factory=lambda: ["title", "body", "custom_fields.*"], max_length=64
    )
    exclude_fields: list[str] = Field(default_factory=list, max_length=64)
    entry_type_ids: list[str] = Field(default_factory=list, max_length=64)
    metadata_fields: list[str] = Field(default_factory=list, max_length=32)
    model_config = {"extra": "forbid"}

    @field_validator("fields", "exclude_fields", "metadata_fields")
    @classmethod
    def valid_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            if path not in {"title", "body", "custom_fields.*"} and not re.fullmatch(
                r"custom_fields\.[A-Za-z0-9_-]{1,128}", path
            ):
                raise ValueError("Index fields must name canonical text fields")
        return list(dict.fromkeys(paths))

    @field_validator("metadata_fields")
    @classmethod
    def exact_metadata_paths(cls, paths: list[str]) -> list[str]:
        if any(
            not p.startswith("custom_fields.") or p == "custom_fields.*" for p in paths
        ):
            raise ValueError("Metadata fields must name explicit custom fields")
        return paths


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    revision: str
    dimensions: int
    chunk_tokens: int
    overlap_tokens: int
    format_version: int = 1

    @property
    def id(self) -> str:
        return digest(asdict(self))


def embedding_profile() -> EmbeddingProfile:
    """Resolve and validate deployment embedding settings."""
    from .embedding_model import DEFAULT_MODEL_NAME

    profile = EmbeddingProfile(
        model=DEFAULT_MODEL_NAME,
        revision=os.getenv("EMBEDDING_MODEL_REVISION", "bundled-v1"),
        dimensions=int(os.getenv("EMBEDDING_MODEL_DIMENSIONS", "384")),
        chunk_tokens=int(os.getenv("INDEX_CHUNK_TOKENS", "160")),
        overlap_tokens=int(os.getenv("INDEX_OVERLAP_TOKENS", "24")),
    )
    if not 16 <= profile.dimensions <= 2000:
        raise ValueError("Embedding dimensions must be between 16 and 2000")
    if not 32 <= profile.chunk_tokens <= 384:
        raise ValueError("Passage token budget must be between 32 and 384")
    if not 0 <= profile.overlap_tokens < profile.chunk_tokens // 2:
        raise ValueError("Invalid passage overlap")
    return profile


def selected_text(entry: Any, definition: IndexDefinition) -> dict[str, str]:
    """Select only declared canonical text values."""
    custom = getattr(entry, "custom_fields", None) or {}
    values = {"title": entry.title or "", "body": entry.body or ""}
    values.update(
        {f"custom_fields.{k}": v for k, v in custom.items() if isinstance(v, str)}
    )
    selected = {}
    for path, value in values.items():
        allowed = path in definition.fields or (
            path.startswith("custom_fields.") and "custom_fields.*" in definition.fields
        )
        excluded = path in definition.exclude_fields or (
            path.startswith("custom_fields.")
            and "custom_fields.*" in definition.exclude_fields
        )
        if allowed and not excluded and value.strip():
            selected[path] = value
    return selected


def split_passages(
    texts: dict[str, str], profile: EmbeddingProfile, tokenizer: Any
) -> list[dict[str, Any]]:
    """Use the embedding model's tokenizer, preserving every selected token.

    Text offsets preserve Markdown/citations. Oversized content is refused with
    a visible indexing failure instead of silently publishing a partial index.
    """
    result = []
    seen = set()
    for field, text in texts.items():
        if digest(text) in seen:
            continue
        seen.add(digest(text))
        encoding = tokenizer.encode(text, add_special_tokens=False)
        offsets = encoding.offsets
        step = profile.chunk_tokens - profile.overlap_tokens
        for start in range(0, len(offsets), step):
            window = offsets[start : start + profile.chunk_tokens]
            if not window:
                break
            a, b = window[0][0], window[-1][1]
            result.append({"field": field, "start": a, "end": b, "text": text[a:b]})
            if len(result) > 4096:
                raise ValueError("Entry exceeds the supported passage count")
            if start + profile.chunk_tokens >= len(offsets):
                break
    return result
