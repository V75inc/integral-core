"""Shared, versioned presentation contract for package and instance App Homes."""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.dashboards import DashboardWidgetSpec, GridPlacement
from app.schemas.home_native import HomeElement


class HomeNativeWidget(DashboardWidgetSpec):
    @field_validator("type")
    @classmethod
    def native_only(cls, value: str) -> str:
        if value in {"extension_view", "native_component"}:
            raise ValueError("use the extension widget contract")
        return value


class HomeComponentWidget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    type: Literal["native_component"]
    title: str = Field(default="", max_length=200)
    grid: GridPlacement = Field(default_factory=GridPlacement)
    content: HomeElement


class HomeActionCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget: str
    state: Literal["empty", "has_records"]


class HomeDraftAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    draft: str = Field(min_length=1, max_length=500)
    when: Optional[HomeActionCondition] = None

    @field_validator("label", "draft")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class AppHomeComposition(BaseModel):
    """Source of truth shared by the compiler, API and resident tool schemas."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    renderer: Literal["widgets", "native"] = "widgets"
    widgets: List[Union[HomeComponentWidget, HomeNativeWidget]] = Field(
        default_factory=list, max_length=12
    )
    actions: List[HomeDraftAction] = Field(default_factory=list, max_length=3)
    content: Optional[HomeElement] = None
    track_shortcuts: List[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
    ] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_composition(self) -> "AppHomeComposition":
        if not self.title.strip():
            raise ValueError("Home title is required")
        if self.renderer == "native":
            if self.content is None or self.widgets:
                raise ValueError("native Home requires content and no widgets")
        elif self.content is not None or not self.widgets:
            raise ValueError("widget Home requires widgets and no full-view content")
        encoded = json.dumps(self.model_dump(), allow_nan=False).encode("utf-8")
        if len(encoded) > 131072:
            raise ValueError("Home definition exceeds 128 KiB")
        seen: set[str] = set()
        placements: List[Dict[str, int]] = []
        native_ids = set()
        for widget in self.widgets:
            if not widget.id or len(widget.id) > 128 or widget.id in seen:
                raise ValueError("Home widget IDs must be unique and bounded")
            seen.add(widget.id)
            if widget.type != "native_component":
                native_ids.add(widget.id)
            g = widget.grid.model_dump()
            if not (
                0 <= g["x"] < 12
                and 0 <= g["y"] <= 100
                and 1 <= g["w"] <= 12
                and 1 <= g["h"] <= 20
                and g["x"] + g["w"] <= 12
            ):
                raise ValueError("Home widget placement is out of bounds")
            if any(
                g["x"] < p["x"] + p["w"]
                and p["x"] < g["x"] + g["w"]
                and g["y"] < p["y"] + p["h"]
                and p["y"] < g["y"] + g["h"]
                for p in placements
            ):
                raise ValueError("Home widgets must not overlap")
            placements.append(g)
        if len({action.label for action in self.actions}) != len(self.actions):
            raise ValueError("Home action labels must be unique")
        if any(
            action.when and action.when.widget not in native_ids
            for action in self.actions
        ):
            raise ValueError("Home action condition must name a native widget")
        return self


class HomePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: Dict[str, Any]
    include_data: bool = False


class ExtensionNavigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["app", "track", "entry", "dashboard"]
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")


class ScaffoldHome(BaseModel):
    """Plain-text starting guide for a newly scaffolded App."""

    model_config = ConfigDict(extra="forbid")
    introduction: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    steps: List[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
        ]
    ] = Field(default_factory=list, max_length=5)

    track_shortcuts: List[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
    ] = Field(default_factory=list, max_length=5)
    actions: List[HomeDraftAction] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_shortcuts(self) -> "ScaffoldHome":
        if len({name.casefold() for name in self.track_shortcuts}) != len(
            self.track_shortcuts
        ):
            raise ValueError("Home Track names must be unique")
        if any(action.when for action in self.actions):
            raise ValueError("Scaffold Home shortcuts cannot depend on a widget")
        if len({action.label for action in self.actions}) != len(self.actions):
            raise ValueError("Home action labels must be unique")
        return self

    def composition(self) -> Dict[str, Any]:
        return AppHomeComposition(
            title="Home",
            description=self.introduction if self.steps else "",
            renderer="native",
            actions=self.actions,
            track_shortcuts=self.track_shortcuts,
            content=HomeElement(
                id="scaffold-start",
                type="stack",
                children=(
                    [
                        HomeElement(
                            id="scaffold-heading", type="heading", text="Start here"
                        ),
                        *[
                            HomeElement(id=f"scaffold-step-{i}", type="text", text=step)
                            for i, step in enumerate(self.steps, start=1)
                        ],
                    ]
                    if self.steps
                    else [
                        HomeElement(
                            id="scaffold-intro", type="text", text=self.introduction
                        )
                    ]
                ),
            ),
        ).model_dump(mode="json", exclude_none=True)
