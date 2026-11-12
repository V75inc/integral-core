"""Opt-in criteria for an EntryType representing a standing goal."""

from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from app.contracts.task_semantics import FieldId, FieldOutcome, TitleOutcome

GOAL_COMPLETED_STATUS = "completed"


class EvidenceEquals(BaseModel):
    """An exact typed value on the supporting Entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["equals"]
    field_id: FieldId
    value: StrictBool | StrictInt | StrictStr


class EvidenceCount(BaseModel):
    """A recorded nonnegative integer count on the supporting Entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["recorded_count"]
    field_id: FieldId


class GoalCriterion(BaseModel):
    """Bind one criterion to an Entry relation on the goal and a source predicate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: FieldId
    evidence_relation_field_id: FieldId
    predicate: Annotated[EvidenceEquals | EvidenceCount, Field(discriminator="kind")]


class GoalSemantics(BaseModel):
    """Declare the outcome and the evidence required to verify it."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Annotated[int, Field(ge=1, le=1)]
    outcome: Annotated[TitleOutcome | FieldOutcome, Field(discriminator="source")]
    criteria: Annotated[list[GoalCriterion], Field(min_length=1, max_length=8)]


def validate_goal_semantics(
    raw: Any, fields: Sequence[Mapping[str, Any]]
) -> GoalSemantics:
    """Resolve goal-side fields by stable identity, independent of display keys."""
    semantics = GoalSemantics.model_validate(raw)
    if isinstance(semantics.outcome, FieldOutcome):
        matches = [f for f in fields if f.get("id") == semantics.outcome.field_id]
        if len(matches) != 1 or matches[0].get("type") != "text":
            raise ValueError("goal outcome must reference one text field")
    if len({criterion.key for criterion in semantics.criteria}) != len(
        semantics.criteria
    ):
        raise ValueError("goal criteria must have unique keys")
    for criterion in semantics.criteria:
        matches = [
            f for f in fields if f.get("id") == criterion.evidence_relation_field_id
        ]
        if len(matches) != 1 or matches[0].get("type") != "relation":
            raise ValueError("goal evidence must reference one relation field")
        relation = matches[0].get("relation")
        if (
            not isinstance(relation, Mapping)
            or relation.get("target") != "entry"
            or relation.get("many", False)
        ):
            raise ValueError(
                "goal evidence must reference a single Entry relation field"
            )
    return semantics
