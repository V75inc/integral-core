"""Unit tests for plan-agnostic AI credit metering."""

from __future__ import annotations

import math

import pytest

from app.services import ai_usage
from app.services.ai_usage import (
    WINDOW_DAYS,
    credits_for,
    model_weight_for,
    register_ai_usage_limit_resolver,
)


def test_model_weight_light_default_heavy():
    assert model_weight_for("openai/gpt-4o-mini") == 1.0
    assert model_weight_for("openai/gpt-4.1") == 2.0
    assert model_weight_for("openai/o1-preview") == 4.0
    assert model_weight_for("") == 2.0


def test_credits_for_ceil_and_min_one():
    # 100 input + 0 output, weight 1 → ceil(100/1000)=1 → min 1
    assert credits_for(100, 0, "openai/gpt-4o-mini") == 1
    # 500 in + 100 out → weighted 500+300=800, weight 2 → ceil(1600/1000)=2
    assert credits_for(500, 100, "openai/gpt-4.1") == 2
    assert credits_for(0, 0, "openai/gpt-4.1") == 0
    # explicit weight override
    assert credits_for(1000, 0, "anything", model_weight=1.0) == 1


def test_credits_math_matches_formula():
    in_tok, out_tok, weight = 2500, 500, 2.0
    expected = max(1, int(math.ceil((in_tok + 3 * out_tok) * weight / 1000.0)))
    assert credits_for(in_tok, out_tok, "x", model_weight=weight) == expected


@pytest.mark.asyncio
async def test_no_resolver_means_unlimited(monkeypatch):
    register_ai_usage_limit_resolver(None)

    async def _zero(_ws: str, **_kwargs) -> int:
        return 0

    monkeypatch.setattr(ai_usage, "platform_credits_used", _zero)
    snap = await ai_usage.snapshot("n.Workspace.test")
    assert snap.is_unlimited
    assert snap.enforcement_enabled is False
    await ai_usage.assert_within_quota("n.Workspace.test")


@pytest.mark.asyncio
async def test_assert_within_quota_raises_when_exhausted(monkeypatch):
    async def _limit(_ws: str) -> int:
        return 100

    async def _used(_ws: str, **_kwargs) -> int:
        return 100

    register_ai_usage_limit_resolver(_limit)
    monkeypatch.setattr(ai_usage, "platform_credits_used", _used)
    with pytest.raises(Exception) as excinfo:
        await ai_usage.assert_within_quota("n.Workspace.test")
    assert getattr(excinfo.value, "error_code", "") == "ai_quota_exceeded"
    register_ai_usage_limit_resolver(None)


@pytest.mark.asyncio
async def test_soft_warning_at_eighty_percent(monkeypatch):
    async def _limit(_ws: str) -> int:
        return 100

    async def _used(_ws: str, **_kwargs) -> int:
        return 80

    register_ai_usage_limit_resolver(_limit)
    monkeypatch.setattr(ai_usage, "platform_credits_used", _used)
    snap = await ai_usage.snapshot("n.Workspace.test")
    assert snap.is_soft_warning
    assert not snap.is_exhausted
    assert snap.window_days == WINDOW_DAYS
    register_ai_usage_limit_resolver(None)
