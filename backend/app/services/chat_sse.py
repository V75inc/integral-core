"""Dependency-free encoding and settled-outcome reads for public chat SSE."""

import codecs
import json
from collections.abc import AsyncIterable
from typing import Any, Dict, Optional, Tuple


def sse_bytes(event: str, data: Dict[str, Any]) -> bytes:
    """Encode a named SSE event frame with JSON-serialized payload."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


async def settled_chat_turn_outcome(
    chunks: AsyncIterable[bytes | str], *, max_frame_chars: int = 1024 * 1024
) -> Tuple[bool, Optional[str]]:
    """Drain a chat stream; EOF alone cannot establish successful execution.

    Keep only one bounded event frame, not the response text or tool history.
    Success requires one final succeeded acknowledgement and no error event.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    settled_status: Optional[str] = None
    error: Optional[str] = None

    def accept_frame(frame: str) -> None:
        nonlocal settled_status, error
        if len(frame) > max_frame_chars:
            raise ValueError("Chat event frame exceeds its bound")
        event_name = ""
        data_lines = []
        for line in frame.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            if key == "event":
                event_name = value.removeprefix(" ")
            elif key == "data":
                data_lines.append(value.removeprefix(" "))
        if not data_lines:
            if event_name:
                raise ValueError("Chat event has no payload")
            return  # Comments and heartbeat frames do not settle execution.
        payload = json.loads("\n".join(data_lines))
        if not isinstance(payload, dict):
            raise ValueError("Chat event payload must be an object")
        if settled_status is not None:
            raise ValueError("Chat stream contains events after settlement")
        kind = str(payload.get("type") or event_name)
        if kind == "error" or event_name == "error":
            message = payload.get("message") or payload.get("code")
            error = (
                message[:512]
                if isinstance(message, str)
                else "Agent turn reported an error"
            )
        if kind == "turn-settled" or event_name == "turn-settled":
            status = payload.get("status")
            if not isinstance(status, str) or status not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                raise ValueError("Chat settlement status is unavailable")
            settled_status = status

    async for chunk in chunks:
        text = decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        if not isinstance(text, str):
            raise ValueError("Chat stream returned an invalid chunk")
        buffer = (buffer + text).replace("\r\n", "\n")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            accept_frame(frame)
        if len(buffer) > max_frame_chars:
            raise ValueError("Chat event frame exceeds its bound")
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        raise ValueError("Chat stream ended inside an event frame")
    if error:
        return False, error
    if settled_status == "succeeded":
        return True, None
    if settled_status == "cancelled":
        return False, "Agent turn was cancelled"
    return False, "Agent turn did not settle successfully"
