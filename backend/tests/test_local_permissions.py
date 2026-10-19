"""Local runtime directory privacy contracts."""

import os
import stat

import pytest

from app import local_permissions


def test_private_directory_posix(tmp_path):
    """POSIX uses owner-only mode with no group or other access."""
    if os.name == "nt":
        pytest.skip("POSIX contract")
    local_permissions.private_directory(tmp_path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_windows_branch_uses_native_acl_without_chmod(tmp_path, monkeypatch):
    """Windows delegates to native ACL protection rather than POSIX chmod."""
    seen = []
    monkeypatch.setattr(local_permissions, "_windows_private_directory", seen.append)
    # Restore os.name before pytest/pathlib interacts with the host filesystem.
    with monkeypatch.context() as patch:
        patch.setattr(local_permissions.os, "name", "nt")
        local_permissions.private_directory(tmp_path)
    assert seen == [tmp_path]


def test_native_acl_errors_are_not_silently_ignored(tmp_path, monkeypatch):
    """A failed privacy operation prevents startup."""

    def fail(_path):
        raise OSError("synthetic ACL failure")

    monkeypatch.setattr(local_permissions, "_windows_private_directory", fail)
    with monkeypatch.context() as patch:
        patch.setattr(local_permissions.os, "name", "nt")
        with pytest.raises(OSError, match="ACL failure"):
            local_permissions.private_directory(tmp_path)
