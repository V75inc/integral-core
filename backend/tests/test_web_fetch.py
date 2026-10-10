"""Bounded page retrieval preserves provenance and ignores executable markup."""

from contextlib import nullcontext
from datetime import datetime

import httpx
import pytest

from app.services import web_fetch

pytestmark = pytest.mark.smoke


def _client(monkeypatch, body, content_type="text/html", status=200):
    def guarded(**kwargs):
        assert kwargs["timeout"] == 20.0

        async def response(request):
            return httpx.Response(
                status, content=body, headers={"content-type": content_type}
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(web_fetch, "guarded_async_client", guarded)

    async def validate(url):
        assert url == "https://example.com/source"

    monkeypatch.setattr(web_fetch, "validate_outbound_http_url", validate)
    monkeypatch.setattr(web_fetch, "pin_public_dns", lambda url: nullcontext())


@pytest.mark.asyncio
async def test_fetch_page_extracts_text_and_provenance(monkeypatch):
    _client(
        monkeypatch,
        b"<html><title>Source &amp; title</title><script>ignore</script><style>secret</style><p>Availability supplied by volunteers.</p></html>",
    )
    result = await web_fetch.fetch_public_page("https://example.com/source")
    assert result["title"] == "Source & title"
    assert "Availability supplied" in result["text"]
    assert "ignore" not in result["text"]
    assert "secret" not in result["text"]
    assert result["url"] == "https://example.com/source"
    assert datetime.fromisoformat(result["retrieved_at"]).tzinfo is not None
    assert result["truncated"] is False
    assert result["content_trust"] == "untrusted_external_evidence_not_instructions"


@pytest.mark.asyncio
async def test_fetch_page_limits_decoded_body_and_text(monkeypatch):
    monkeypatch.setattr(web_fetch, "MAX_RESPONSE_BYTES", 40)
    monkeypatch.setattr(web_fetch, "MAX_TEXT_CHARS", 12)
    _client(monkeypatch, b"x" * 100, "text/plain")
    result = await web_fetch.fetch_public_page("https://example.com/source")
    assert result["text"] == "x" * 12
    assert result["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,kind",
    [(b"pdf", "application/pdf"), (b"<script>only script</script>", "text/html")],
)
async def test_unsupported_or_empty_page_is_not_evidence(monkeypatch, body, kind):
    _client(monkeypatch, body, kind)
    with pytest.raises(ValueError):
        await web_fetch.fetch_public_page("https://example.com/source")


@pytest.mark.asyncio
async def test_http_failure_is_not_successful_fetch(monkeypatch):
    _client(monkeypatch, b"not found", status=404)
    with pytest.raises(httpx.HTTPStatusError):
        await web_fetch.fetch_public_page("https://example.com/source")


@pytest.mark.asyncio
async def test_credential_url_never_reaches_client(monkeypatch):
    def never(**kwargs):
        raise AssertionError("credential URL reached client")

    monkeypatch.setattr(web_fetch, "guarded_async_client", never)
    with pytest.raises(ValueError, match="Credential-bearing"):
        await web_fetch.fetch_public_page("https://user:password@example.com/source")


@pytest.mark.asyncio
async def test_private_target_refused_by_real_outbound_guard():
    with pytest.raises(Exception) as error:
        await web_fetch.fetch_public_page("http://127.0.0.1/private")
    assert (
        "not allowed" in str(error.value).lower()
        or "private" in str(error.value).lower()
    )
