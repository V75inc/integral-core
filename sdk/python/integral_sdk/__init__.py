"""Public SDK for Integral App authors (ADR-011).

Runtime context objects are injected by Core — this package provides typing
helpers and documentation-facing aliases only.
"""

from integral_sdk.capabilities import ToolContextV2
from integral_sdk.context import OperationContext
from integral_sdk.information import (
    FieldDefinition,
    RecordRevision,
    RelationDefinition,
)

__all__ = [
    "FieldDefinition",
    "OperationContext",
    "RecordRevision",
    "RelationDefinition",
    "ToolContextV2",
]
__version__ = "0.2.0"
