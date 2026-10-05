"""chat_streaming exception classification stays provider-neutral."""

from __future__ import annotations

from app.api.errors import QuotaExceededError
from app.services.chat_streaming import _ERROR_MESSAGES, classify_turn_exception


def test_quota_fallback_message_has_no_billing_product_copy():
    msg = _ERROR_MESSAGES["ai_quota_exceeded"]
    assert "Billing" not in msg
    assert "rolling window" not in msg.lower()


def test_classify_prefers_quota_exception_message():
    code, msg = classify_turn_exception(
        QuotaExceededError(message="Host-specific upgrade copy")
    )
    assert code == "ai_quota_exceeded"
    assert msg == "Host-specific upgrade copy"
