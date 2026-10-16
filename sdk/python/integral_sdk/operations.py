"""HTTP/extension bridge operation responses; types confer no authority.

Handler-injected facade protocols keep their own return contracts. These types
apply to the HTTP operation response and the extension bridge's response value.
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union


class OperationReceiptReference(TypedDict):
    """Opaque original journal identity, never an execution permit."""

    id: str
    status: Literal["succeeded"]
    replayed: bool


class RecordedCompletionReference(TypedDict):
    """An observed original completion whose effect was not repeated."""

    id: str
    status: Literal["succeeded"]
    replayed: Literal[True]


class _AvailableOperationFields(TypedDict):
    app_id: str
    operation_key: str
    output: Dict[str, Any]


class AvailableOperationResponse(_AvailableOperationFields, total=False):
    """Currently readable saved output; absence of a discriminator is legacy."""

    output_disposition: Literal["available"]
    current_state_verified: Literal[False]
    operation_receipt: Optional[OperationReceiptReference]
    object_refs: List[Dict[str, Any]]
    evidence: Optional[Dict[str, Any]]
    receipt: Optional[Dict[str, Any]]


class WithheldOperationResponse(TypedDict):
    """Past completion only; no saved output or current domain state proof."""

    output_disposition: Literal["withheld"]
    current_state_verified: Literal[False]
    operation_receipt: RecordedCompletionReference


OperationResponse = Union[AvailableOperationResponse, WithheldOperationResponse]
