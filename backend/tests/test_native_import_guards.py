"""Tests for early native-import guards."""

import importlib
import sys

import app._native_import_guards as guards


def test_windows_blocks_magic_import_by_default(monkeypatch) -> None:
    monkeypatch.setattr(guards.sys, "platform", "win32")
    monkeypatch.delenv("ATTACHMENT_LIBMAGIC_ENABLED", raising=False)
    guards._GUARDS_INSTALLED = False
    guards._MAGIC_BLOCKER = None
    for name in list(sys.modules):
        if name == "magic" or name.startswith("magic."):
            del sys.modules[name]

    guards.install_native_import_guards()

    try:
        importlib.import_module("magic")
        assert False, "expected ImportError"
    except ImportError as exc:
        assert "disabled on Windows" in str(exc)


def test_explicit_enable_skips_block_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(guards.sys, "platform", "linux")
    monkeypatch.setenv("ATTACHMENT_LIBMAGIC_ENABLED", "1")
    guards._GUARDS_INSTALLED = False
    guards._MAGIC_BLOCKER = None

    guards.install_native_import_guards()

    assert guards._MAGIC_BLOCKER is None
