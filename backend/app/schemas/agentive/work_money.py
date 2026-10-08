"""Shared exact USD primitives for mandate pricing and reservation accounting."""

from decimal import Decimal, localcontext
from typing import Annotated

from pydantic import Field

Money = Annotated[
    Decimal, Field(ge=0, allow_inf_nan=False, max_digits=18, decimal_places=8)
]


def money_units(amount: Decimal) -> int:
    """Exact USD units at 1e-8, independent of ambient decimal precision."""
    with localcontext() as context:
        context.prec = 36
        scaled = amount * Decimal(100000000)
        if scaled != scaled.to_integral_value():
            raise ValueError("cost exceeds USD precision")
        return int(scaled)
