"""Ed25519 verification of bundle dirs (Spec §4.1, §11).

Canonical payload = sha256 over a sorted file manifest where each entry is
``<relative_path>\\0<sha256_hex>\\0<size>\\n``. Stable across machines.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PUBKEY_ENV = "INTEGRAL_PROFILE_PUBKEY"
_SIGNATURE_FILENAME = "signature.bin"
_PAYLOAD_EXCLUDE = {_SIGNATURE_FILENAME}


@dataclass(frozen=True)
class SignatureResult:
    verified: bool
    reason: str  # "dev_mode" | "valid" | "missing_signature.bin" | "verify_failed" | "no_python"


def _bundle_ships_python(bundle_dir: Path) -> bool:
    skills = bundle_dir / "skills"
    if not skills.exists():
        return False
    for p in skills.rglob("*.py"):
        if p.is_file():
            return True
    return False


def compute_bundle_payload(bundle_dir: Path) -> bytes:
    """Return the canonical sha256 payload digest for files under ``bundle_dir``.

    Excludes ``signature.bin``; ordering by relative path keeps the
    digest stable across machines per Spec §4.1.
    """
    entries = []
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(bundle_dir).as_posix()
        if rel in _PAYLOAD_EXCLUDE:
            continue
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        entries.append(f"{rel}\x00{sha}\x00{len(data)}".encode())
    return hashlib.sha256(b"\n".join(entries) + b"\n").digest()


def verify_bundle_signature(
    bundle_dir: Path,
    pubkey_b64: Optional[str],
) -> SignatureResult:
    """Verify the Ed25519 signature attached to ``bundle_dir``.

    Dev mode (no ``pubkey_b64``) and Python-free bundles short-circuit
    to ``verified=True`` per Spec §11; everything else requires a
    valid ``signature.bin``.
    """
    if not pubkey_b64:
        return SignatureResult(True, "dev_mode")
    if not _bundle_ships_python(bundle_dir):
        return SignatureResult(True, "no_python")
    sig_path = bundle_dir / _SIGNATURE_FILENAME
    if not sig_path.exists():
        return SignatureResult(False, "missing_signature.bin")
    try:
        from nacl.encoding import Base64Encoder
        from nacl.signing import VerifyKey

        vk = VerifyKey(pubkey_b64.encode(), encoder=Base64Encoder)
        payload = compute_bundle_payload(bundle_dir)
        signature = sig_path.read_bytes()
        vk.verify(payload, signature)
        return SignatureResult(True, "valid")
    except Exception as exc:
        logger.warning("bundle %s signature verify failed: %s", bundle_dir.name, exc)
        return SignatureResult(False, "verify_failed")
