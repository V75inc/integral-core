"""Opt-in, versioned task semantics for a published EntryType."""

from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

RESIDENT_ASSIGNMENT_CHANGED = "entry.resident_assignment.changed"
RESIDENT_ASSIGNMENT_SETTINGS_CHANGED = "workspace.resident_assignment_settings.changed"

FieldId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TitleOutcome(BaseModel):
    """Read the task outcome from the platform title."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["title"]


class FieldOutcome(BaseModel):
    """Read the task outcome from a stable custom-field identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["field"]
    field_id: FieldId


class TaskSemantics(BaseModel):
    """Map task inputs without granting assignment or execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    outcome: Annotated[TitleOutcome | FieldOutcome, Field(discriminator="source")]
    acceptance_criteria_field_id: FieldId | None = None
    due_date_field_id: FieldId | None = None
    dependencies_field_id: FieldId | None = None

    @field_validator("version", mode="before")
    @classmethod
    def require_integer_version(cls, value: Any) -> Any:
        """Reject boolean and string aliases for the wire version."""
        if type(value) is not int:
            raise ValueError("task semantics version must be an integer")
        return value


def validate_task_semantics(
    raw: Any, fields: Sequence[Mapping[str, Any]]
) -> TaskSemantics:
    """Resolve declared field IDs exactly once against normalized fields."""
    semantics = TaskSemantics.model_validate(raw)
    outcome_id = (
        semantics.outcome.field_id
        if isinstance(semantics.outcome, FieldOutcome)
        else None
    )
    roles = (
        ("outcome", outcome_id, {"text"}),
        (
            "acceptance criteria",
            semantics.acceptance_criteria_field_id,
            {"text"},
        ),
        ("due date", semantics.due_date_field_id, {"date"}),
        ("dependencies", semantics.dependencies_field_id, {"relation"}),
    )
    for role, field_id, types in roles:
        if field_id is None:
            continue
        matching = [field for field in fields if field.get("id") == field_id]
        if len(matching) != 1 or matching[0].get("type") not in types:
            raise ValueError(f"{role} must reference one field of the declared type")
        if role == "dependencies":
            relation = matching[0].get("relation")
            if not isinstance(relation, Mapping) or relation.get("target") != "entry":
                raise ValueError("dependencies must reference an Entry relation field")
    return semantics
