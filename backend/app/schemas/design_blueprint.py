"""Typed App design blueprint recorded by ``integral_propose_design`` (W1.3).

The markdown preview is what the user reads; the blueprint is what the builder
and later verification compare against. Every constituent carries an ``id``
that stays stable across amendments, so a revision is a diff of item IDs
rather than a re-read of prose. Optional constituents are absent or empty;
platform defaults the build relies on are listed explicitly.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Iterator, List, Literal, Optional, Tuple

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ItemId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$",
        max_length=96,
    ),
]
FieldKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$", max_length=64
    ),
]
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ItemId


class BlueprintApp(_Item):
    name: Label
    description: Optional[Text] = None


class BlueprintGoal(_Item):
    text: Text


class BlueprintActor(_Item):
    name: Label
    role: Optional[Text] = None


class BlueprintRelation(BaseModel):
    """Lookup (``target: entry``) or expansion (``target: track``) reference."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["entry", "track"]
    target_entry_types: List[Label] = Field(default_factory=list)
    target_track: Optional[ItemId] = None
    target_track_template: Optional[ItemId] = None
    many: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "BlueprintRelation":
        if self.target == "entry" and not self.target_entry_types:
            raise ValueError("an entry relation names target_entry_types")
        if self.target == "track" and not self.target_track_template:
            raise ValueError("a track relation names target_track_template")
        if self.target == "entry" and self.target_track_template:
            raise ValueError("target_track_template is only for target: track")
        return self


class BlueprintField(_Item):
    key: FieldKey
    name: Label
    type: Label
    required: bool = False
    options: List[Label] = Field(
        default_factory=list, validation_alias=AliasChoices("options", "enum")
    )
    relation: Optional[BlueprintRelation] = None

    @model_validator(mode="after")
    def _relation_matches_type(self) -> "BlueprintField":
        if (self.type == "relation") != (self.relation is not None):
            raise ValueError(f"field {self.key}: relation config iff type is relation")
        return self


class BlueprintEntryType(_Item):
    name: Label
    fields: List[BlueprintField] = Field(default_factory=list)


class BlueprintTagGroup(_Item):
    name: Label
    tags: List[Label] = Field(min_length=1)


class BlueprintTrack(_Item):
    name: Label
    description: Optional[Text] = None
    entry_types: List[BlueprintEntryType] = Field(min_length=1)
    tag_groups: List[BlueprintTagGroup] = Field(default_factory=list)


class BlueprintTrackTemplate(BlueprintTrack):
    """Detail-Track shape provisioned per parent Entry through ``ANCHORS``."""


class BlueprintView(_Item):
    track: ItemId
    name: Label
    type: Label
    decision: Text
    config: Dict[str, Any] = Field(default_factory=dict)


class BlueprintWidget(_Item):
    title: Label
    type: Label
    track: Optional[ItemId] = None
    decision: Text


class BlueprintDashboard(_Item):
    name: Label
    widgets: List[BlueprintWidget] = Field(min_length=1)


class BlueprintSkill(_Item):
    name: Label
    purpose: Text
    visibility: Literal["app_private", "workspace"] = "app_private"


class BlueprintRoutine(_Item):
    name: Label
    purpose: Text
    cron: Optional[Label] = None
    run_at: Optional[Label] = None

    @model_validator(mode="after")
    def _one_schedule(self) -> "BlueprintRoutine":
        if bool(self.cron) == bool(self.run_at):
            raise ValueError(f"routine {self.id}: give exactly one of cron or run_at")
        return self


class BlueprintSeed(_Item):
    track: ItemId
    title: Label
    fields: Dict[FieldKey, Any] = Field(default_factory=dict)


class BlueprintOperation(_Item):
    name: Label
    purpose: Text


class BlueprintAccess(_Item):
    subject: Label
    grant: Label


class BlueprintOpenDecision(_Item):
    question: Text


class BlueprintPlatformDefault(_Item):
    kind: Label
    detail: Text


class DesignBlueprint(BaseModel):
    """Machine form of an approved App design; see module docstring."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    app: BlueprintApp
    goals: List[BlueprintGoal] = Field(default_factory=list)
    actors: List[BlueprintActor] = Field(default_factory=list)
    tracks: List[BlueprintTrack] = Field(min_length=1)
    track_templates: List[BlueprintTrackTemplate] = Field(default_factory=list)
    views: List[BlueprintView] = Field(default_factory=list)
    dashboard: Optional[BlueprintDashboard] = None
    skills: List[BlueprintSkill] = Field(default_factory=list)
    routines: List[BlueprintRoutine] = Field(default_factory=list)
    seeds: List[BlueprintSeed] = Field(default_factory=list)
    operations: List[BlueprintOperation] = Field(default_factory=list)
    access: List[BlueprintAccess] = Field(default_factory=list)
    open_decisions: List[BlueprintOpenDecision] = Field(default_factory=list)
    platform_defaults: List[BlueprintPlatformDefault] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_schema_ids(cls, data: Any) -> Any:
        """Entry types and fields may omit ``id``; derive it from the Track id.

        A field's key is already its identity within a Track, so
        ``<track_id>.<key>`` is as stable as the key itself.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for section in ("tracks", "track_templates"):
            tracks = data.get(section)
            if not isinstance(tracks, list):
                continue
            filled_tracks = []
            for track in tracks:
                if not isinstance(track, dict) or not isinstance(track.get("id"), str):
                    filled_tracks.append(track)
                    continue
                entry_types = []
                for entry_type in track.get("entry_types") or []:
                    if not isinstance(entry_type, dict):
                        entry_types.append(entry_type)
                        continue
                    slug = "_".join(str(entry_type.get("name") or "").lower().split())
                    fields = [
                        (
                            {**f, "id": f"{track['id']}.{f['key']}"}
                            if isinstance(f, dict)
                            and "id" not in f
                            and isinstance(f.get("key"), str)
                            else f
                        )
                        for f in entry_type.get("fields") or []
                    ]
                    entry_types.append(
                        {
                            "id": f"{track['id']}.type.{slug}",
                            **entry_type,
                            "fields": fields,
                        }
                    )
                filled_tracks.append({**track, "entry_types": entry_types})
            data[section] = filled_tracks
        return data

    def items(self) -> Iterator[Tuple[str, BaseModel]]:
        """Yield ``(item_id, constituent)`` for every identified constituent."""
        yield self.app.id, self.app
        for track in [*self.tracks, *self.track_templates]:
            yield track.id, track
            for entry_type in track.entry_types:
                yield entry_type.id, entry_type
                for field in entry_type.fields:
                    yield field.id, field
            for group in track.tag_groups:
                yield group.id, group
        for section in (
            self.goals,
            self.actors,
            self.views,
            self.skills,
            self.routines,
            self.seeds,
            self.operations,
            self.access,
            self.open_decisions,
            self.platform_defaults,
        ):
            for item in section:
                yield item.id, item
        if self.dashboard:
            yield self.dashboard.id, self.dashboard
            for widget in self.dashboard.widgets:
                yield widget.id, widget

    @model_validator(mode="after")
    def _references_resolve(self) -> "DesignBlueprint":
        ids: List[str] = [item_id for item_id, _ in self.items()]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"item ids must be unique: {', '.join(duplicates)}")
        tracks = {track.id: track for track in self.tracks}
        templates = {template.id for template in self.track_templates}
        names = [track.name.casefold() for track in self.tracks]
        if len(set(names)) != len(names):
            raise ValueError("track names must be unique")
        for track in [*self.tracks, *self.track_templates]:
            keys = [f.key for et in track.entry_types for f in et.fields]
            if len(set(keys)) != len(keys):
                raise ValueError(f"track {track.id}: field keys must be unique")
            for field in (f for et in track.entry_types for f in et.fields):
                relation = field.relation
                if (
                    relation
                    and relation.target_track
                    and relation.target_track not in tracks
                ):
                    raise ValueError(f"field {field.id}: unknown target_track")
                if (
                    relation
                    and relation.target_track_template
                    and relation.target_track_template not in templates
                ):
                    raise ValueError(f"field {field.id}: unknown target_track_template")
        widget_tracks = [
            w.track for w in (self.dashboard.widgets if self.dashboard else [])
        ]
        for label, track_id in [
            *((f"view {v.id}", v.track) for v in self.views),
            *((f"seed {s.id}", s.track) for s in self.seeds),
            *(("dashboard widget", t) for t in widget_tracks if t),
        ]:
            if track_id not in tracks:
                raise ValueError(f"{label}: unknown track {track_id}")
        for seed in self.seeds:
            known = {f.key for et in tracks[seed.track].entry_types for f in et.fields}
            unknown = sorted(set(seed.fields) - known)
            if unknown:
                raise ValueError(
                    f"seed {seed.id}: unknown field keys {', '.join(unknown)}"
                )
        return self
