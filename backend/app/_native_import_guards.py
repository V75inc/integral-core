"""Early import guards for native extensions that can crash the process.

``python-magic`` loads ``libmagic`` via ``ctypes.CDLL``. A broken or
incompatible ``libmagic`` DLL on Windows can trigger an access violation
during ``import magic`` — before Python's ``try/except`` runs. jvspatial's
file validator imports ``magic`` at module load time, so the crash happens
during normal API startup, not only when sniffing uploads.

When libmagic is disabled (the default on Windows), we install a meta-path
hook that turns ``import magic`` into a clean ``ImportError`` so callers
fall back to ``mimetypes`` / client-supplied MIME types.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_GUARDS_INSTALLED = False
_MAGIC_BLOCKER: Optional["_BlockMagicImport"] = None


def _magic_explicitly_enabled() -> bool:
    return os.environ.get("ATTACHMENT_LIBMAGIC_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _magic_explicitly_disabled() -> bool:
    return os.environ.get("ATTACHMENT_LIBMAGIC_ENABLED", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _should_block_magic_import() -> bool:
    if sys.platform not in ("win32", "cygwin"):
        return False
    if _magic_explicitly_enabled():
        return False
    if _magic_explicitly_disabled():
        return True
    # Default off on Windows — broken libmagic installs are common on dev boxes.
    return True


def _probe_magic_import() -> bool:
    """Return True only when ``import magic`` succeeds in a child process."""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", "import magic"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("libmagic startup probe failed (%s)", exc)
        return False
    return completed.returncode == 0


class _BlockMagicLoader(importlib.abc.Loader):
    def create_module(self, spec):  # noqa: ANN001, ARG002
        return None

    def exec_module(self, module):  # noqa: ANN001, ARG002
        raise ImportError(
            "python-magic is disabled on Windows because libmagic can crash "
            "the process during import. Set ATTACHMENT_LIBMAGIC_ENABLED=1 "
            "after installing a working libmagic build to opt in."
        )


class _BlockMagicImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001, ARG002
        if fullname == "magic" or fullname.startswith("magic."):
            return importlib.machinery.ModuleSpec(fullname, _BlockMagicLoader())
        return None


def install_native_import_guards() -> None:
    """Install process-wide guards once, as early as possible in startup."""
    global _GUARDS_INSTALLED, _MAGIC_BLOCKER

    if _GUARDS_INSTALLED:
        return
    _GUARDS_INSTALLED = True

    if not _should_block_magic_import():
        if sys.platform in ("win32", "cygwin") and _magic_explicitly_enabled():
            if _probe_magic_import():
                logger.info("libmagic probe succeeded; python-magic import allowed")
                return
            logger.warning(
                "ATTACHMENT_LIBMAGIC_ENABLED=1 but libmagic probe failed; "
                "blocking python-magic import to keep the API bootable"
            )
        else:
            return

    _MAGIC_BLOCKER = _BlockMagicImport()
    sys.meta_path.insert(0, _MAGIC_BLOCKER)
    logger.info(
        "Blocking python-magic import on Windows; MIME detection falls back "
        "to mimetypes / client-supplied Content-Type"
    )
