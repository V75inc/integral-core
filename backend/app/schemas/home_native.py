"""Native, data-only Home components rendered by Integral's own UI system."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    type: Literal["text", "textarea", "number", "email", "checkbox"] = "text"
    required: bool = False


class HomeElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: Literal[
        "stack",
        "grid",
        "card",
        "heading",
        "text",
        "markdown",
        "metric",
        "chart",
        "records",
        "form",
        "action",
        "tabs",
        "accordion",
    ]
    title: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=10000)
    children: List[HomeElement] = Field(default_factory=list, max_length=128)
    columns: int = Field(default=2, ge=1, le=4)
    gap: Literal["sm", "md", "lg"] = "md"
    query_key: Optional[str] = Field(default=None, max_length=128)
    params: Dict[str, Any] = Field(default_factory=dict)
    value_path: str = Field(default="total_estimate", max_length=200)
    rows_path: str = Field(default="items", max_length=200)
    label_field: str = Field(default="title", max_length=128)
    value_field: Optional[str] = Field(default=None, max_length=128)
    chart_type: Literal["bar", "line", "pie"] = "bar"
    fields: List[HomeInput] = Field(default_factory=list, max_length=24)
    operation_key: Optional[str] = Field(default=None, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)
    submit_label: str = Field(default="Submit", max_length=80)

    @model_validator(mode="after")
    def coherent(self) -> HomeElement:
        if (
            self.type in {"heading", "text", "markdown"}
            and not (self.text or self.title).strip()
        ):
            raise ValueError("Home text components require content")
        containers = {"stack", "grid", "card", "tabs", "accordion"}
        if self.type in containers and not self.children:
            raise ValueError("Home container requires children")
        if self.type not in containers and self.children:
            raise ValueError("Home leaf cannot contain children")
        if self.type in {"metric", "chart", "records"} and not self.query_key:
            raise ValueError("Home data component requires a declared query")
        if self.type in {"form", "action"} and not self.operation_key:
            raise ValueError("Home action requires a declared operation")
        if self.type == "form" and not self.fields:
            raise ValueError("Home form requires fields")
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Home form fields must be unique")
        return self
