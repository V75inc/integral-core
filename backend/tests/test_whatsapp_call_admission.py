"""Unit tests for pure WhatsApp caller admission. No graph, HTTP, or app boot."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.agentive.services.whatsapp_call_admission import (
    CallerAdmission,
    admit_whatsapp_caller,
)

_VERIFIED = "+15551212000"
_WORKSPACE = "ws-1"
_PRINCIPAL = "user-1"
_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "agentive"
    / "services"
    / "whatsapp_call_admission.py"
)


def _admit(caller: str, **overrides) -> CallerAdmission:
    kwargs = {
        "verified_e164": _VERIFIED,
        "line_enabled": True,
        "calling_enabled": True,
        "ambiguous_verified_number": False,
        "workspace_id": _WORKSPACE,
        "ambiguous_workspace": False,
        "principal_id": _PRINCIPAL,
    }
    kwargs.update(overrides)
    return admit_whatsapp_caller(caller, **kwargs)


def _assert_refused(result: CallerAdmission, reason: str) -> None:
    assert result.decision == "refuse"
    assert result.reason == reason
    assert result.verified_e164 == ""
    assert result.workspace_id == ""
    assert result.principal_id == ""


@pytest.mark.unit
def test_exact_match_with_both_flags_answers():
    """A matching caller on an enabled calling line is answered."""
    result = _admit(_VERIFIED)

    assert result.decision == "answer"
    assert result.reason == "answer"
    assert result.verified_e164 == _VERIFIED
    assert result.workspace_id == _WORKSPACE
    assert result.principal_id == _PRINCIPAL
    assert isinstance(result.workspace_id, str)


@pytest.mark.unit
@pytest.mark.parametrize(
    "caller",
    [
        "15551212000",
        "+15551212000",
        "+1 555 121 2000",
        "+1 (555) 121-2000",
    ],
)
def test_equivalent_spellings_match(caller: str):
    """Digits match with or without a plus, spaces, or punctuation."""
    result = _admit(caller)

    assert result.decision == "answer"
    assert result.reason == "answer"
    assert result.verified_e164 == _VERIFIED
    assert result.workspace_id == _WORKSPACE
    assert result.principal_id == _PRINCIPAL


@pytest.mark.unit
def test_different_number_refuses_caller_mismatch():
    """A different number is a mismatch, not an answer."""
    result = _admit("+19995550000")

    _assert_refused(result, "caller_mismatch")


@pytest.mark.unit
def test_empty_caller_refuses():
    """A blank caller is refused before any other check."""
    result = _admit("")

    _assert_refused(result, "empty_caller")


@pytest.mark.unit
@pytest.mark.parametrize("caller", ["1234567", "1" * 16, "not-a-phone"])
def test_non_normalizable_caller_refuses_invalid(caller: str):
    """Too-short, too-long, and non-numeric callers are invalid."""
    result = _admit(caller)

    _assert_refused(result, "invalid_caller")


@pytest.mark.unit
def test_empty_verified_number_refuses():
    """An empty personal number refuses even when the caller looks dialable."""
    result = _admit(_VERIFIED, verified_e164="")

    _assert_refused(result, "no_verified_number")


@pytest.mark.unit
def test_ambiguous_verified_number_refuses():
    """More than one verified identity refuses before a digit comparison."""
    result = _admit(_VERIFIED, ambiguous_verified_number=True)

    _assert_refused(result, "ambiguous_verified_number")


@pytest.mark.unit
def test_disabled_line_refuses_matching_caller():
    """A matching number on a disabled line is refused."""
    result = _admit(_VERIFIED, line_enabled=False)

    _assert_refused(result, "line_disabled")


@pytest.mark.unit
def test_calling_disabled_refuses_enabled_line():
    """Calling off refuses a matching caller on an enabled text line."""
    result = _admit(_VERIFIED, calling_enabled=False)

    _assert_refused(result, "calling_disabled")


@pytest.mark.unit
def test_missing_workspace_refuses():
    """No workspace id refuses a caller who otherwise matches."""
    result = _admit(_VERIFIED, workspace_id="")

    _assert_refused(result, "no_workspace")


@pytest.mark.unit
def test_ambiguous_workspace_refuses():
    """More than one enabled grant refuses, and an answer holds one id."""
    result = _admit(_VERIFIED, ambiguous_workspace=True, workspace_id=_WORKSPACE)

    _assert_refused(result, "ambiguous_workspace")


@pytest.mark.unit
def test_business_number_alone_cannot_answer():
    """display_phone is not an argument; an empty personal number refuses."""
    parameters = inspect.signature(admit_whatsapp_caller).parameters
    assert "display_phone" not in parameters
    assert "phone_number_id" not in parameters

    result = _admit("+15550001111", verified_e164="")

    _assert_refused(result, "no_verified_number")


@pytest.mark.unit
def test_empty_caller_precedes_later_failures():
    """empty_caller wins when later checks would also fail."""
    result = _admit(
        "",
        verified_e164="",
        ambiguous_verified_number=True,
        line_enabled=False,
        calling_enabled=False,
        workspace_id="",
        ambiguous_workspace=True,
    )

    _assert_refused(result, "empty_caller")


@pytest.mark.unit
def test_invalid_caller_precedes_identity_and_line_checks():
    """A non-normalizable caller is invalid before verified-number checks."""
    result = _admit(
        "123",
        verified_e164="",
        ambiguous_verified_number=True,
        line_enabled=False,
        calling_enabled=False,
    )

    _assert_refused(result, "invalid_caller")


@pytest.mark.unit
def test_ambiguous_identity_precedes_empty_verified_number():
    """The ambiguous-identity flag wins over an empty verified number."""
    result = _admit(_VERIFIED, verified_e164="", ambiguous_verified_number=True)

    _assert_refused(result, "ambiguous_verified_number")


@pytest.mark.unit
def test_mismatch_precedes_disabled_line_and_calling():
    """A different number is a mismatch even when the line cannot answer."""
    result = _admit(
        "+19995550000",
        line_enabled=False,
        calling_enabled=False,
        workspace_id="",
        ambiguous_workspace=True,
    )

    _assert_refused(result, "caller_mismatch")


@pytest.mark.unit
def test_line_disabled_precedes_calling_and_workspace():
    """A disabled line wins over calling off and a missing workspace."""
    result = _admit(
        _VERIFIED,
        line_enabled=False,
        calling_enabled=False,
        workspace_id="",
        ambiguous_workspace=True,
    )

    _assert_refused(result, "line_disabled")


@pytest.mark.unit
def test_calling_disabled_precedes_workspace_checks():
    """Calling off wins over an ambiguous or missing workspace."""
    result = _admit(
        _VERIFIED,
        calling_enabled=False,
        workspace_id="",
        ambiguous_workspace=True,
    )

    _assert_refused(result, "calling_disabled")


@pytest.mark.unit
def test_ambiguous_workspace_precedes_missing_workspace():
    """The ambiguous-workspace flag wins over an empty workspace id."""
    result = _admit(_VERIFIED, workspace_id="", ambiguous_workspace=True)

    _assert_refused(result, "ambiguous_workspace")


@pytest.mark.unit
def test_module_does_not_import_jvconnect_or_native_voice():
    """Admission stays pure: no jvconnect client and no voice relay."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(alias.name for alias in node.names)
    joined = " ".join(sorted(imported))
    assert "jvconnect_whatsapp" not in joined
    assert "native_voice" not in joined
    source = _MODULE.read_text(encoding="utf-8")
    assert "resolve_channel_identity" not in source
    assert "ChannelIdentity.find" not in source


@pytest.mark.unit
def test_uses_whatsapp_line_calling_enabled_defaults_false():
    """Calling is a separate flag and starts off. Text enablement stays enabled."""
    from app.agentive.edges import UsesWhatsAppLine

    grant = UsesWhatsAppLine()
    assert grant.calling_enabled is False
    assert grant.enabled is True

    grants = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "connectors"
        / "bundled_packages"
        / "whatsapp"
        / "adapter"
        / "grants.py"
    ).read_text(encoding="utf-8")
    assert "grant.enabled and binding.enabled" in grants
    assert "calling_enabled" not in grants

    binding = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "services"
        / "whatsapp_channel_binding.py"
    ).read_text(encoding="utf-8")
    setter = binding.split("async def set_workspace_line_enabled", 1)[1]
    setter = setter.split("\nasync def ", 1)[0]
    assert "calling_enabled" not in setter
