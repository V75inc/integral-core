"""Scan connector roots and admit manifests that match the v1 schema.

Discovery has no lifecycle side effects. A rejected package is recorded with
a reason and is not offered for install. Declarative packages are admitted
when the manifest is valid. Native code belongs to the Core build.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA = Path(__file__).with_name("integral-connector-v1.schema.json")


def connector_roots(extra: list[Path] | None = None) -> list[Path]:
    """Explicit overrides, configured paths, then the working-directory default."""
    roots: list[Path] = []
    if extra:
        roots.extend(extra)
    env = os.environ.get("INTEGRAL_CONNECTOR_PATHS", "").strip()
    if env:
        roots.extend(Path(part.strip()) for part in env.split(",") if part.strip())
    default = Path.cwd() / "integral-connectors"
    if default.is_dir():
        roots.append(default)
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


@dataclass
class PackageAdmission:
    """One scan. Admitted packages are installable; rejected ones are not."""

    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)


_validator: Draft202012Validator | None = None


def _schema_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        import json

        schema = json.loads(_SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        _validator = Draft202012Validator(schema)
    return _validator


_FIRST_PARTY = Path(__file__).resolve().parent / "bundled_packages"


def first_party_package_dir(package_id: str) -> Path | None:
    """Return a first-party package directory. A user root never matches."""
    if not package_id or "/" in package_id or ".." in package_id:
        return None
    path = (_FIRST_PARTY / package_id).resolve()
    if path.parent != _FIRST_PARTY.resolve() or not path.is_dir():
        return None
    return path


def load_package(
    directory: Path, *, allow_trusted: bool = False
) -> tuple[dict[str, Any] | None, str]:
    """Return (manifest, "") or (None, reason)."""
    bundled = (
        allow_trusted and first_party_package_dir(directory.name) == directory.resolve()
    )
    manifest_path = directory / "connector.yaml"
    if not bundled and (directory.is_symlink() or manifest_path.is_symlink()):
        return None, "operator connector folders and manifests must not be symlinks"
    if not manifest_path.is_file():
        return None, "connector.yaml is missing"
    try:
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, yaml.YAMLError):
        return None, "connector.yaml is not valid YAML"
    except OSError:
        return None, "connector.yaml could not be read"
    if not isinstance(document, dict):
        return None, "connector.yaml must be a mapping"
    errors = sorted(
        _schema_validator().iter_errors(document), key=lambda item: list(item.path)
    )
    if errors:
        first: ValidationError = errors[0]
        place = ".".join(str(part) for part in first.path) or "manifest"
        return None, f"{place}: {first.message}"
    if document["id"] != directory.name:
        return None, "folder name must equal the manifest id"
    specifier = str(document.get("core") or "").strip()
    if specifier:
        reason = _core_requirement(specifier)
        if reason:
            return None, reason
    if not bundled:
        if document["trust"]["tier"] != "declarative":
            return None, "operator connector folders must be declarative"
        for path in directory.iterdir():
            if (
                path.name not in {"connector.yaml", "README.md", "icon.svg", "icon.png"}
                or not path.is_file()
            ):
                return (
                    None,
                    "operator connector folders may contain only connector.yaml, README.md, and icon files",
                )
    return document, ""


def _core_requirement(specifier: str) -> str:
    """Return a reason when this Core version is outside the package specifier."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    from app import __version__

    try:
        accepted = SpecifierSet(specifier)
    except InvalidSpecifier:
        return "core specifier is invalid"
    if __version__ not in accepted:
        return f"core requires {specifier}"
    return ""


_PACKAGE_ID = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_SCAFFOLDS = {
    "actions": """
http:
  base_url: https://example.com
capabilities:
  actions:
  - name: ping
    description: Read one resource from the service.
    effect: read
    input: {type: object}
    request: {method: GET, path: /ping}
""",
    "mcp": """
capabilities:
  mcp:
    transport: streamable_http
    url: https://example.com/mcp
    read_only_tools: []
""",
    "sync": """
http:
  base_url: https://example.com
capabilities:
  sync:
    entry_type: record
    source:
      request: {method: GET, path: /records}
      items: $.items
    map:
      external_id: $.id
      title: $.name
""",
    "channel": """
http:
  base_url: https://example.com
configuration:
- key: webhook_secret
  label: Webhook secret
  type: secret
  required: true
auth:
  type: api_key
  secret: webhook_secret
  inject: {in: header, name: Authorization, prefix: "Bearer "}
capabilities:
  channel:
    mode: webhook
    ingress:
      verify: {scheme: hmac_sha256, header: X-Signature, secret: webhook_secret}
      sender: $.from
      text: $.text
    send:
      request: {method: POST, path: /messages}
""",
}


def scaffold_package(directory: Path, package_id: str, kind: str) -> str:
    """Write a new declarative package. Return a reason when it cannot be created."""
    if kind not in _SCAFFOLDS:
        return "kind must be actions, mcp, sync, or channel"
    if _PACKAGE_ID.fullmatch(package_id) is None:
        return "package id must match the manifest id pattern"
    if directory.exists():
        return "package directory already exists"
    name = " ".join(part.capitalize() for part in package_id.split("_"))
    document = (
        "schema: integral.connector/v1\n"
        f"id: {package_id}\n"
        "version: 0.1.0\n"
        "publisher: local\n"
        f"name: {name}\n"
        f"description: Scaffolded {kind} connector. Replace this before use.\n"
        "trust: {tier: declarative}\n"
        "binding:\n"
        "  scopes: [workspace]\n"
        f"{_SCAFFOLDS[kind].strip()}\n"
    )
    directory.mkdir(parents=True)
    manifest_path = directory / "connector.yaml"
    manifest_path.write_text(document)
    reason = validate_package_dir(directory)
    if reason:
        manifest_path.unlink()
        directory.rmdir()
        return reason
    return ""


def validate_package_dir(directory: Path) -> str:
    """Return an empty string when the package would be admitted."""
    _manifest, reason = load_package(directory)
    return reason


def scan_packages(roots: list[Path] | None = None) -> PackageAdmission:
    """Admit each valid declarative package once. Duplicate ids are rejected."""
    admission = PackageAdmission()
    if roots is None:
        sourced = [(_FIRST_PARTY, True), *[(root, False) for root in connector_roots()]]
    else:
        sourced = [(root, False) for root in roots]
    bundled_ids = {path.parent.name for path in _FIRST_PARTY.glob("*/connector.yaml")}
    seen: set[str] = set()
    for root, allow_trusted in sourced:
        if not root.is_dir():
            continue
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest, reason = load_package(child, allow_trusted=allow_trusted)
            package_id = child.name
            if manifest is None:
                admission.rejected.append({"id": package_id, "reason": reason})
                continue
            if package_id in seen or (not allow_trusted and package_id in bundled_ids):
                if package_id not in bundled_ids:
                    admission.packages.pop(package_id, None)
                    admission.fingerprints.pop(package_id, None)
                admission.rejected.append(
                    {"id": package_id, "reason": "duplicate package id"}
                )
                continue
            seen.add(package_id)
            admission.packages[package_id] = manifest
            admission.fingerprints[package_id] = hashlib.sha256(
                (child / "connector.yaml").read_bytes()
            ).hexdigest()
    return admission


_current = PackageAdmission()


def start_package_discovery() -> PackageAdmission:
    """Initialize the admission snapshot during Core boot."""
    global _current
    _current = scan_packages()
    return _current


def current_admission() -> PackageAdmission:
    """Return the admission cache from the last scan."""
    return _current


def rescan(roots: list[Path] | None = None) -> PackageAdmission:
    """Replace declarative admissions. Trusted packages stay as they were at startup."""
    global _current
    previous = _current
    fresh = scan_packages(roots)
    if previous.packages or previous.rejected:
        for package_id, manifest in list(fresh.packages.items()):
            if (manifest.get("trust") or {}).get("tier") != "trusted":
                continue
            prior = previous.packages.get(package_id)
            if prior is not None:
                fresh.packages[package_id] = prior
                prior_fingerprint = previous.fingerprints.get(package_id)
                if prior_fingerprint:
                    fresh.fingerprints[package_id] = prior_fingerprint
            else:
                fresh.packages.pop(package_id, None)
                fresh.fingerprints.pop(package_id, None)
                fresh.rejected.append(
                    {
                        "id": package_id,
                        "reason": "trusted packages load only at startup",
                    }
                )
    _current = fresh
    return _current
