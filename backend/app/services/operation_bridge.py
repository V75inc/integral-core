"""Operation specification and developer package skeleton (W1.7 Phase A).

A blueprint operation is behaviour this chat cannot install. Phase A writes
the contract a developer would implement — typed inputs and outputs, the
policy action, idempotency, effects, the conflict rule, and the tests that
lock them — and renders a package skeleton from that contract. Nothing here
registers a tool or claims the action is live.
"""

from __future__ import annotations

import json
import keyword
from typing import Any, Dict, List, Mapping, Optional, get_args

import yaml

from app.exceptions import OperationalModelValidationError
from app.schemas.design_blueprint import DesignBlueprint
from app.schemas.policy import PolicyAction
from app.services.operational_model_compile import (
    _parse_manifest_operations,
    slug_manifest_key,
)

_POLICY_ACTIONS = set(get_args(PolicyAction))
_UNSPECIFIED_POLICY = "unspecified"
_CONFLICT_RULE = (
    "The same idempotency key with a different payload is a conflict "
    "and does not apply a second time."
)
_CONTRACT_KEYS = (
    "input_schema",
    "output_schema",
    "policy_action",
    "idempotency_key",
    "effects",
    "conflict_rule",
    "tests",
)


def _schema(fields: List[Mapping[str, Any]], *, default_name: str) -> Dict[str, Any]:
    rows = list(fields) or [{"name": default_name, "type": "string", "required": True}]
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        properties[name] = {"type": str(row.get("type") or "string")}
        if row.get("required"):
            required.append(name)
    if not properties:
        properties[default_name] = {"type": "string"}
        required = [default_name]
    out: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


def _policy(value: Optional[str]) -> str:
    text = (value or "").strip()
    if text in _POLICY_ACTIONS:
        return text
    return _UNSPECIFIED_POLICY


def specify_operation(
    operation: Mapping[str, Any], *, key: Optional[str] = None
) -> Dict[str, Any]:
    """Build one operation contract from a blueprint operation.

    ``purpose`` is the prose request. Typed fields, when the blueprint
    already has them, are kept; otherwise the contract uses a single
    record id in and a receipt out. The conflict rule and the two tests
    are part of every spec so a protected transition is specified even
    when the prose does not name its fields.
    """
    name = str(operation.get("name") or "").strip()
    purpose = str(operation.get("purpose") or "").strip()
    op_key = (key or slug_manifest_key(name) or "operation").strip()
    effects = [
        str(item).strip()
        for item in (operation.get("effects") or [])
        if str(item).strip()
    ]
    if purpose and purpose not in effects:
        effects.insert(0, purpose)
    conflict = str(operation.get("conflict_rule") or "").strip() or _CONFLICT_RULE
    outputs = list(operation.get("outputs") or []) or [
        {"name": "ok", "type": "boolean", "required": True},
        {"name": "receipt_id", "type": "string", "required": True},
    ]
    return {
        "key": op_key,
        "kind": "execute",
        "name": name,
        "description": purpose,
        "policy_action": _policy(operation.get("policy_action")),
        "staging_level": "required",
        "idempotency_key": "supported",
        "timeout_seconds": 30,
        "tool": op_key,
        "input_schema": _schema(
            list(operation.get("inputs") or []), default_name="record_id"
        ),
        "output_schema": _schema(outputs, default_name="ok"),
        "effects": effects,
        "conflict_rule": conflict,
        "tests": [
            {
                "name": "applies_once",
                "expect": "one successful result and one effect",
            },
            {
                "name": "same_key_different_payload",
                "expect": "conflict, and no second effect",
            },
        ],
        "live": False,
    }


def operation_specs(blueprint: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One spec per blueprint operation. Duplicate names get a stable suffix."""
    seen: Dict[str, int] = {}
    specs: List[Dict[str, Any]] = []
    for raw in blueprint.get("operations") or []:
        if not isinstance(raw, Mapping):
            continue
        base = slug_manifest_key(str(raw.get("name") or "")) or "operation"
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}_{seen[base]}"
        specs.append(specify_operation(raw, key=key))
    return specs


def _function_name(key: str) -> str:
    if key.isidentifier() and not keyword.iskeyword(key):
        return key
    return f"op_{key}"


def _stub(spec: Mapping[str, Any]) -> str:
    key = str(spec["key"])
    fn = _function_name(key)
    purpose = json.dumps(spec.get("description") or "")
    return (
        f"def {fn}(**payload):\n"
        f'    """Developer stub. Not a live tool.\n'
        f"\n"
        f"    {purpose}\n"
        f'    """\n'
        f"    raise NotImplementedError({json.dumps(key)})\n"
    )


def _contract_test() -> str:
    keys = ", ".join(repr(key) for key in _CONTRACT_KEYS)
    return f'''"""Generated contract for the operation skeleton. Not an installed tool."""

from pathlib import Path

import yaml

_REQUIRED = ({keys})


def test_operation_contract():
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((root / "operational-model.yaml").read_text())
    operations = (raw.get("app") or {{}}).get("operations") or []
    assert operations, "skeleton has no operations"
    for operation in operations:
        for key in _REQUIRED:
            assert operation.get(key), key
        assert operation["input_schema"]["type"] == "object"
        assert operation["output_schema"]["type"] == "object"
        assert operation["idempotency_key"] == "supported"
        names = {{item["name"] for item in operation["tests"]}}
        assert "applies_once" in names
        assert "same_key_different_payload" in names
'''


def render_package_skeleton(
    blueprint: Mapping[str, Any], specs: List[Mapping[str, Any]]
) -> Dict[str, str]:
    """Files for a developer package. Keys are paths inside the package root."""
    app = blueprint.get("app") if isinstance(blueprint.get("app"), Mapping) else {}
    app_name = str((app or {}).get("name") or "Custom add-on").strip()
    slug = slug_manifest_key(app_name) or "custom-add-on"
    manifest = {
        "integral_operational_model_version": 3,
        "scope": "app",
        "package": {
            "name": app_name,
            "slug": slug,
            "class": "private_org_app",
            "version": "0.1.0",
            "trust_tier": "trusted",
            "description": ("Generated operation skeleton. Specified, not installed."),
        },
        "app": {
            "description": "Developer skeleton for behaviour chat cannot install.",
            "operations": [dict(spec) for spec in specs],
        },
    }
    stubs = "\n\n".join(_stub(spec) for spec in specs) or (
        "def _empty():\n    return None\n"
    )
    return {
        "operational-model.yaml": yaml.safe_dump(
            manifest, sort_keys=False, allow_unicode=True
        ),
        "tools/__init__.py": "",
        "tools/operations.py": stubs + "\n",
        "tests/test_operation_contract.py": _contract_test(),
    }


def validate_skeleton(files: Mapping[str, str]) -> List[Dict[str, Any]]:
    """Parse the skeleton the way an installed manifest is parsed.

    Raises ValueError when the package slug is missing, the stub module
    does not compile, or an operation fails the manifest operation parser.
    Returns the parsed operations (compiler fields only).
    """
    raw = yaml.safe_load(files["operational-model.yaml"])
    package = raw.get("package") if isinstance(raw, dict) else None
    if not isinstance(package, dict) or not str(package.get("slug") or "").strip():
        raise ValueError("skeleton package.slug is required")
    source = files.get("tools/operations.py") or ""
    compile(source, "tools/operations.py", "exec")  # syntax only; not executed
    operations = ((raw.get("app") or {}).get("operations")) or []
    try:
        parsed = _parse_manifest_operations(operations)
    except (OperationalModelValidationError, ValueError) as exc:
        raise ValueError(f"skeleton operations failed to parse: {exc}") from exc
    if len(parsed) != len(operations):
        raise ValueError("skeleton dropped an operation")
    return parsed


def operation_bridge(blueprint: Any) -> Optional[Dict[str, Any]]:
    """Specs plus skeleton files, or None when the design has no operations."""
    if isinstance(blueprint, DesignBlueprint):
        data = blueprint.model_dump(mode="json")
    elif isinstance(blueprint, Mapping):
        data = dict(blueprint)
    else:
        return None
    specs = operation_specs(data)
    if not specs:
        return None
    files = render_package_skeleton(data, specs)
    validate_skeleton(files)
    return {
        "live": False,
        "specs": specs,
        "files": files,
    }


def bridge_artifact_body(bridge: Mapping[str, Any]) -> str:
    """JSON body stored on the conversation artifact."""
    return json.dumps(
        {"live": False, "specs": bridge["specs"], "files": bridge["files"]},
        ensure_ascii=False,
    )
