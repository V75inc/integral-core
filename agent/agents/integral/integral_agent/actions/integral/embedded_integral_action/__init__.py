"""Embedded counterpart of integral_api_action — talks to the host
Integral backend in-process via direct function calls instead of HTTP.

See ``embedded_integral_action.py`` for the full surface.
"""

from .embedded_integral_action import EmbeddedIntegralAction

__all__ = ["EmbeddedIntegralAction"]
