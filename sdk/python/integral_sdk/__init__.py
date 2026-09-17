"""Public SDK for Integral App authors (ADR-011).

Runtime context objects are injected by Core — this package provides typing
helpers and documentation-facing aliases only.
"""

from integral_sdk.context import OperationContext

__all__ = ["OperationContext"]
__version__ = "0.1.0"
