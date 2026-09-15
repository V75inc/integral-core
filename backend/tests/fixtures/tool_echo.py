"""Echo tool — fixture for tool_dispatch tests."""

from typing import Any, Dict


async def echo(input: Dict[str, Any], ctx) -> Dict[str, Any]:
    return {"echoed": input.get("msg", "")}
