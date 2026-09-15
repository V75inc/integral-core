"""Tests for ``flag_if_locked`` (payroll structural redesign) — the
detective-only entry.update hook that flags edits to a payslip whose Pay
Run is paid, or a filing line whose filing is submitted/accepted, via an
audit-visible ChangeEvent. See ``tools/_filing_lock.py``'s docstring for
why this cannot reject the edit outright (no substrate primitive for a
bundle to block entry.update today).
"""

from __future__ import annotations

import pytest

from app.profiles.payroll_app.tools.lock_audit import flag_if_locked


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, track_id="track-1"):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.track_id = track_id


class _FakeCtx:
    def __init__(self, *, entries, filings=None, audits=None):
        self._entries = entries
        self._filings = filings or []
        self.audit_calls = audits if audits is not None else []

    async def get_entry_system(self, entry_id):
        for e in [*self._entries, *self._filings]:
            if e.id == entry_id:
                return e
        return None

    async def find_entries_in_track_type(self, track_type):
        return {"filings": self._filings}.get(track_type, [])

    async def emit_audit(self, action, details):
        self.audit_calls.append((action, details))


@pytest.mark.asyncio
async def test_flags_payslip_edit_when_pay_run_paid():
    pay_run = _FakeEntry("pay-run-1", {"status": "paid"})
    payslip = _FakeEntry("payslip-1", {"pay_run": "pay-run-1"})
    ctx = _FakeCtx(entries=[pay_run, payslip])

    result = await flag_if_locked(
        {"entry_id": "payslip-1", "entry_type": "payslip"}, ctx
    )

    assert result == {"ok": True, "locked": True}
    assert len(ctx.audit_calls) == 1
    action, details = ctx.audit_calls[0]
    assert action == "entry.update"
    assert details["locked_edit"] is True
    assert details["entry_id"] == "payslip-1"
    assert details["entry_type"] == "payslip"


@pytest.mark.asyncio
async def test_does_not_flag_payslip_when_pay_run_not_paid():
    pay_run = _FakeEntry("pay-run-2", {"status": "approved"})
    payslip = _FakeEntry("payslip-2", {"pay_run": "pay-run-2"})
    ctx = _FakeCtx(entries=[pay_run, payslip])

    result = await flag_if_locked(
        {"entry_id": "payslip-2", "entry_type": "payslip"}, ctx
    )

    assert result == {"ok": True, "locked": False}
    assert ctx.audit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_type", ["nis_schedule_line", "paye_filing_line"])
@pytest.mark.parametrize("status", ["submitted", "accepted"])
async def test_flags_filing_line_edit_when_filing_locked(entry_type, status):
    filing = _FakeEntry(
        "filing-1", {"employee_lines_track": "track-locked", "status": status}
    )
    line = _FakeEntry("line-1", {}, track_id="track-locked")
    ctx = _FakeCtx(entries=[line], filings=[filing])

    result = await flag_if_locked({"entry_id": "line-1", "entry_type": entry_type}, ctx)

    assert result == {"ok": True, "locked": True}
    assert len(ctx.audit_calls) == 1


@pytest.mark.asyncio
async def test_does_not_flag_filing_line_when_filing_still_draft():
    filing = _FakeEntry(
        "filing-2", {"employee_lines_track": "track-open", "status": "draft"}
    )
    line = _FakeEntry("line-2", {}, track_id="track-open")
    ctx = _FakeCtx(entries=[line], filings=[filing])

    result = await flag_if_locked(
        {"entry_id": "line-2", "entry_type": "nis_schedule_line"}, ctx
    )

    assert result == {"ok": True, "locked": False}
    assert ctx.audit_calls == []


@pytest.mark.asyncio
async def test_unrecognized_entry_type_is_untouched_noop():
    entry = _FakeEntry("other-1", {})
    ctx = _FakeCtx(entries=[entry])

    result = await flag_if_locked(
        {"entry_id": "other-1", "entry_type": "some_other_type"}, ctx
    )

    assert result == {"ok": True, "locked": False}
    assert ctx.audit_calls == []


@pytest.mark.asyncio
async def test_missing_entry_id_is_clean_failure():
    ctx = _FakeCtx(entries=[])
    result = await flag_if_locked({}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_entry_not_found_is_clean_failure():
    ctx = _FakeCtx(entries=[])
    result = await flag_if_locked(
        {"entry_id": "does-not-exist", "entry_type": "payslip"}, ctx
    )
    assert result["ok"] is False
