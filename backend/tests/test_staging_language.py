"""Stage-safe wording and dispatch enrichment for filing tools."""

from __future__ import annotations

from app.agentive.staging_language import (
    enrich_filing_tool_result,
    ensure_staged_wording,
    staged_change_reply_hint,
)


def test_ensure_staged_wording_rewrites_filed_to():
    raw = "Filed to 'Source Material' as Source"
    out = ensure_staged_wording(raw)
    assert "Proposed filing to" in out
    assert "pending your approval" in out
    assert "Filed" not in out


def test_staged_change_reply_hint_forbids_filed_language():
    hint = staged_change_reply_hint('File content as "X" in Track')
    assert "Staged" in hint
    assert "not filed" in hint.lower()


def test_enrich_filing_tool_result_adds_reply_hint_on_staged_change():
    data = {
        "_kind": "staged_change",
        "summary": 'File content as "Title" in People',
        "token": "tok",
    }
    out = enrich_filing_tool_result("integral_file_content", data)
    assert out["assistant_reply_hint"]
    assert "Staged" in out["assistant_reply_hint"]
