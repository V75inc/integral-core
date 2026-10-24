"""Bounded native embedding transport with explicit owned credentials only."""

from __future__ import annotations

import asyncio
import json
import math
import os

import httpx

from app.services.credential_crypto import decrypt_secret_from_storage

from .embedding_store import EmbeddingStoreUnavailable


class WorkspaceEmbeddingUnavailable(EmbeddingStoreUnavailable):
    """A workspace provider failure must not disable other workspaces' search."""


async def encode_profile(texts: list[str], profile, *, require_active: bool = True):
    """Encode a bounded batch with the current owned model and validate every vector."""
    from .embedding_configuration import encoding_authorized

    if require_active and not await encoding_authorized(profile):
        raise WorkspaceEmbeddingUnavailable("The search model is no longer authorized")
    if not profile.provider:
        from .embedding_model import encode_passages

        return await encode_passages(texts, profile.dimensions)
    from app.services.ai_configuration import _enabled

    from .embedding_configuration import (
        owned_connection,
        owner_subject,
    )

    try:
        _enabled()
        if await owner_subject(profile.workspace_id) != profile.owner_id:
            raise ValueError("Workspace owner changed")
        connection = await owned_connection(profile.owner_id, profile.connection_id)
        key = (
            decrypt_secret_from_storage(connection.api_key_enc, aad=profile.owner_id)
            if connection.api_key_enc
            else ""
        )
        if connection.api_key_enc and not key:
            raise ValueError("Embedding key unavailable")
        if not 1 <= len(texts) <= 32 or any(
            not isinstance(t, str) or not t or len(t) > 100000 for t in texts
        ):
            raise ValueError("Invalid embedding batch")
        endpoints = {
            "openai": "https://api.openai.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "ollama": "https://ollama.com",
            "ollama_local": os.getenv("OLLAMA_API_BASE", "http://localhost:11434"),
        }
        base = (
            connection.base_url
            if connection.provider == "custom_openai"
            else endpoints[connection.provider]
        )
        if connection.provider == "custom_openai":
            from app.services.model_provider_endpoints import normalize_custom_base_url

            base = normalize_custom_base_url(base)
        ollama = connection.provider in {"ollama", "ollama_local"}
        body = {"model": profile.model, "input": texts}
        if ollama:
            body.update(truncate=False, dimensions=profile.dimensions)
        else:
            body["encoding_format"] = "float"
            if profile.model.split("/")[-1].startswith("text-embedding-3-"):
                body["dimensions"] = profile.dimensions
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        headers["Accept-Encoding"] = "identity"
        timeout = httpx.Timeout(30, connect=5)
        client: httpx.AsyncClient
        if connection.provider == "custom_openai":
            # Match the harness's per-request authority, HTTPS and DNS-pin
            # policy rather than bypassing it in the embedding adapter.
            from app.services.custom_model_transport import CustomModelAsyncClient

            client = CustomModelAsyncClient(
                base, timeout=timeout, omit_authorization=not bool(key)
            )
        else:
            client = httpx.AsyncClient(
                timeout=timeout, trust_env=False, follow_redirects=False
            )
        from .encoding_runtime import bounded_setting, is_interactive

        deadline = (
            bounded_setting("EMBEDDING_QUERY_TIMEOUT_MS", 1000, 100, 5000) / 1000
            if is_interactive()
            else 40
        )
        async with asyncio.timeout(deadline), client:
            async with client.stream(
                "POST",
                base.rstrip("/") + ("/api/embed" if ollama else "/embeddings"),
                json=body,
                headers=headers,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise ValueError("Compressed embedding responses are unsupported")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise ValueError("Embedding response exceeds its byte budget")
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
        if ollama:
            vectors = data["embeddings"]
        else:
            items = sorted(data["data"], key=lambda item: item["index"])
            if [item["index"] for item in items] != list(range(len(texts))):
                raise ValueError("Invalid embedding response indexes")
            vectors = [item["embedding"] for item in items]
        if len(vectors) != len(texts) or any(
            len(v) != profile.dimensions
            or any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                for x in v
            )
            for v in vectors
        ):
            raise ValueError("Embedding response dimensions or values do not match")
        for vector in vectors:
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("Embedding response contains an invalid vector norm")
        return vectors
    except Exception:
        # No provider body, text, URL or secret in user-visible errors or job status.
        raise WorkspaceEmbeddingUnavailable(
            "Embedding provider unavailable; check the model, key and dimensions in AI Models"
        ) from None
