"""Version-one trusted React component declarations and mount requests."""

import json
from typing import Any, Dict, List, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator

UIPlacement = Literal["home", "track", "entry_compose", "entry_detail"]


class UIComponentDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    entry: str = Field(pattern=r"^ui/[a-zA-Z0-9_/-]+\.mjs$", max_length=240)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_name: str = Field(default="default", pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$")
    sdk_range: Literal["^1.0.0", "^1.1.0", "^1.2.0"] = "^1.0.0"
    placements: List[UIPlacement] = Field(min_length=1, max_length=4)
    props_schema: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )

    @field_validator("entry")
    @classmethod
    def contained_path(cls, value: str) -> str:
        if ".." in value.split("/") or "//" in value:
            raise ValueError("UI entry must be a contained package path")
        return value

    @field_validator("props_schema")
    @classmethod
    def local_schema(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.operational_model_compile import _validate_local_props_schema

        if value.get("type") != "object":
            raise ValueError("component props require an object schema")
        if len(json.dumps(value).encode()) > 32768:
            raise ValueError("component schema exceeds 32 KiB")
        _validate_local_props_schema(value)
        jsonschema.Draft202012Validator.check_schema(value)
        return value


class UIComponentMountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placement: UIPlacement
    props: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("props")
    @classmethod
    def bounded_props(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(json.dumps(value, allow_nan=False).encode()) > 131072:
            raise ValueError("component props exceed 128 KiB")
        return value


class UIComponentMountResponse(BaseModel):
    enabled: bool
    component: UIComponentDeclaration
    artifact_url: str = ""
    definition_revision: str = ""
