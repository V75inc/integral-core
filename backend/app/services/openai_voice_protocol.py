"""OpenAI conversational voice wire protocols, independent of call authority.

Live's transcript deltas and delegation notices are observations. The application
owns turn admission, policy, durable work, review and playback evidence.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode

PCM_RATE = 24_000
MAX_PCM_BYTES = 96_000
MAX_EVENT_BYTES = 1_048_576


class VoiceWireProtocol(StrEnum):
    """Fixed provider routes, never inferred from arbitrary URLs/model prefixes."""

    REALTIME = "realtime"
    LIVE = "live"


REALTIME_MODELS = frozenset(
    {
        "gpt-realtime",
        "gpt-realtime-mini",
        "gpt-realtime-1.5",
        "gpt-realtime-2",
        "gpt-realtime-2.1",
        "gpt-realtime-2.1-mini",
    }
)
LIVE_MODELS = frozenset({"gpt-live-1", "gpt-live-1.1"})


def model_protocol(model: str) -> VoiceWireProtocol | None:
    """Return a documented conversational protocol, not transcription/translation."""
    if model in REALTIME_MODELS:
        return VoiceWireProtocol.REALTIME
    if model in LIVE_MODELS:
        return VoiceWireProtocol.LIVE
    return None


class VoiceSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


async def connect_voice_socket(
    model: str, api_key: str, *, protocol: VoiceWireProtocol
) -> VoiceSocket:
    """Open the fixed official server-owned endpoint with no provider fallback."""
    from websockets.asyncio.client import connect

    if model_protocol(model) != protocol:
        raise ValueError(
            "Model does not support the selected conversational voice protocol"
        )
    endpoint = (
        "wss://api.openai.com/v1/realtime?" + urlencode({"model": model})
        if protocol == VoiceWireProtocol.REALTIME
        else "wss://api.openai.com/v1/live/sessions"
    )
    return await connect(
        endpoint,
        additional_headers={"Authorization": f"Bearer {api_key}"},
        max_size=MAX_EVENT_BYTES,
        open_timeout=10,
        close_timeout=2,
        proxy=None,
    )


@dataclass(frozen=True)
class LiveAudio:
    pcm: bytes


@dataclass(frozen=True)
class LiveTranscript:
    speaker: str
    delta: str
    start_ms: int
    end_ms: int
    event_id: str


@dataclass(frozen=True)
class LiveDelegation:
    identifier: str
    offset_ms: int
    event_id: str


@dataclass(frozen=True)
class LiveClosed:
    reason: str
    usage: dict[str, Any]


@dataclass(frozen=True)
class LiveAppendAccepted:
    event_id: str
    offset_ms: int
    spoken: bool


LiveEvent = (
    LiveAudio | LiveTranscript | LiveDelegation | LiveClosed | LiveAppendAccepted
)


class LiveProtocolError(RuntimeError):
    """A bounded protocol refusal; never echo provider payload or credentials."""


@dataclass(frozen=True)
class CompletedCallerTurn:
    """A host-committed utterance, not an event inferred from a Live fragment.

    The call host must supply a verified completed turn boundary before using it
    with the delegation ledger. The boundary does not grant action authority.
    """

    identifier: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class LiveTaskRequest:
    """One delegation bound to one completed caller utterance."""

    delegation_id: str
    turn: CompletedCallerTurn
    provider_delegated: bool = True


class LiveTurnLedger:
    """Join provider observations with independently committed caller turns.

    This ledger never makes a fragment or a provider delegation into an
    executable user turn. Only the host's completed-utterance path may supply
    that boundary, and ordinary resident submission/approval still follows.
    """

    def __init__(self):
        self._fragments: list[LiveTranscript] = []
        self._pending: dict[str, LiveDelegation] = {}
        self._consumed: set[str] = set()
        self._turns: set[str] = set()
        self._committed_end_ms = -1

    def observe(self, event: LiveTranscript | LiveDelegation) -> None:
        """Retain bounded observations; never perform an effect here."""
        if isinstance(event, LiveTranscript):
            if event.speaker != "user" or event.end_ms <= self._committed_end_ms:
                return
            if len(self._fragments) >= 2048:
                raise LiveProtocolError("Live turn transcript budget exhausted")
            self._fragments.append(event)
        elif event.identifier not in self._consumed:
            if event.offset_ms <= self._committed_end_ms:
                return
            if len(self._pending) >= 64:
                raise LiveProtocolError("Live pending delegation budget exhausted")
            self._pending.setdefault(event.identifier, event)

    def transcript_for(self, start_ms: int, end_ms: int) -> str:
        """Return literal fragments within a host-supplied time window.

        The caller must not interpret this projection as transcript completion.
        An overlapping fragment needs a new boundary rather than truncation.
        """
        if not 0 <= start_ms <= end_ms:
            raise LiveProtocolError("Invalid caller turn boundary")
        selected = []
        for fragment in self._fragments:
            if fragment.end_ms <= start_ms or fragment.start_ms >= end_ms:
                continue
            if fragment.start_ms < start_ms or fragment.end_ms > end_ms:
                raise LiveProtocolError("Live transcript crosses caller turn boundary")
            selected.append(fragment.delta)
        text = "".join(selected).strip()
        if len(text) > 16_000:
            raise LiveProtocolError("Live caller turn is too large")
        return text

    def commit(
        self, turn: CompletedCallerTurn, *, accept_explicit_continuation: bool = False
    ) -> LiveTaskRequest | None:
        """Bind at most one delegation to a newly completed, verified utterance.

        Duplicate delegation events for one utterance are consumed together;
        a later utterance must never replay the same request. Ambiguous pending
        observations are not assumed to be a decision about a proposal.
        """
        if (
            not turn.identifier
            or turn.identifier in self._turns
            or turn.start_ms < max(0, self._committed_end_ms)
            or turn.end_ms <= turn.start_ms
            or not turn.text.strip()
            or len(turn.text) > 16_000
        ):
            return None
        if len(self._turns) >= 1024 or len(self._consumed) >= 4096:
            raise LiveProtocolError("Live turn budget exhausted")
        # A Live delegation can be created before the caller has finished
        # speaking. The host commits the whole turn, including corrections
        # received after the delegation signal, before its text can be used.
        candidates = [
            item
            for item in self._pending.values()
            if turn.start_ms <= item.offset_ms <= turn.end_ms
        ]
        self._turns.add(turn.identifier)
        self._committed_end_ms = turn.end_ms
        for key, item in tuple(self._pending.items()):
            if item.offset_ms <= turn.end_ms:
                self._consumed.add(key)
                del self._pending[key]
        self._fragments = [
            item for item in self._fragments if item.end_ms > turn.end_ms
        ]
        if not candidates:
            if accept_explicit_continuation:
                # Explicit caller-authored control commands have their own
                # host correlation; never fabricate a provider delegation.
                return LiveTaskRequest(
                    "local-continuation-" + turn.identifier,
                    turn,
                    provider_delegated=False,
                )
            return None
        selected = min(candidates, key=lambda item: (item.offset_ms, item.identifier))
        return LiveTaskRequest(selected.identifier, turn)


def _text(value: Any, *, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise LiveProtocolError(f"Invalid Live {label}")
    return value


def _time(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 86_400_000:
        raise LiveProtocolError("Invalid Live timeline position")
    return value


class OpenAILiveSession:
    """Primary Live WebSocket: client delegation and continuous PCM only.

    No Realtime event is translated to pretend a transcript fragment is a complete
    response or an approval. The host must authorize each resulting action itself.
    """

    def __init__(self, socket: VoiceSocket, model: str):
        if model_protocol(model) != VoiceWireProtocol.LIVE:
            raise ValueError("Not a documented GPT-Live conversational model")
        self.socket = socket
        self.model = model
        self.session_id = ""
        self.ready = False
        self.closing = False
        self.closed = False
        self._finalized = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._delegations: set[str] = set()
        self._transcripts: set[str] = set()
        self._muted = False

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.socket.send(json.dumps(payload, ensure_ascii=False))

    async def _recv(self) -> dict[str, Any]:
        raw = await self.socket.recv()
        if len(raw) > MAX_EVENT_BYTES:
            raise LiveProtocolError("Live event too large")
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise LiveProtocolError("Invalid Live event") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise LiveProtocolError("Invalid Live event")
        if event["type"] == "error":
            raise LiveProtocolError("Live provider refused a session event")
        return event

    async def start(
        self,
        *,
        instructions: str,
        voice: str,
        timeout: float = 10,
        startup_message: str | None = None,
    ) -> None:
        """Send the documented first message; require the resolved client mode."""
        if self.ready or self.closing or self.closed:
            raise LiveProtocolError("Live session already started or closed")
        try:
            await self._send(
                {
                    "type": "session.start",
                    "session": {
                        "model": self.model,
                        "instructions": instructions,
                        **(
                            {
                                "input": [
                                    {
                                        "type": "message",
                                        "role": "developer",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": startup_message,
                                            }
                                        ],
                                    }
                                ]
                            }
                            if startup_message
                            else {}
                        ),
                        "audio": {
                            "format": {"type": "audio/pcm", "rate": PCM_RATE},
                            "output": {"voice": voice},
                        },
                        "delegation": {
                            "type": "client",
                            "acknowledgement_delay_ms": 800,
                        },
                        "store": False,
                    },
                }
            )
            async with asyncio.timeout(timeout):
                while True:
                    event = await self._recv()
                    if event["type"] == "info":
                        continue
                    if event["type"] != "session.started":
                        raise LiveProtocolError("Live session did not start")
                    session = event.get("session")
                    if not isinstance(session, dict):
                        raise LiveProtocolError("Live session configuration missing")
                    audio = session.get("audio") or {}
                    delegation = session.get("delegation") or {}
                    if (
                        session.get("model") != self.model
                        or not isinstance(delegation, dict)
                        or not isinstance(audio, dict)
                        or delegation.get("type") != "client"
                        or audio.get("format")
                        != {"type": "audio/pcm", "rate": PCM_RATE}
                    ):
                        raise LiveProtocolError("Live session configuration mismatch")
                    self.session_id = _text(
                        session.get("id"), limit=256, label="session ID"
                    )
                    self.ready = True
                    return
        except BaseException:
            self.closed = True
            await self.socket.close()
            raise

    def _require_ready(self) -> None:
        if not self.ready or self.closing or self.closed:
            raise LiveProtocolError("Live session unavailable")

    async def append_audio(self, pcm: bytes) -> None:
        self._require_ready()
        if self._muted:
            return
        if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
            raise LiveProtocolError("Invalid input PCM frame")
        await self._send(
            {
                "type": "session.input_audio.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def mute(self, muted: bool) -> None:
        self._require_ready()
        if self._muted == muted:
            return
        self._muted = muted
        await self._send(
            {
                "type": (
                    "session.input_audio.mute"
                    if muted
                    else "session.input_audio.unmute"
                )
            }
        )

    async def append_context(
        self, content: str, *, delegation_id: str | None, spoken: bool, event_id: str
    ) -> None:
        """Return a bounded host-verified update; acceptance is not spoken proof."""
        self._require_ready()
        if delegation_id is not None and delegation_id not in self._delegations:
            raise LiveProtocolError("Unknown Live delegation")
        # Conservative byte bound under the API's 500-token append limit.
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.encode("utf-8")) > 480
        ):
            raise LiveProtocolError(
                "Live context update must be a short coherent passage"
            )
        await self._send(
            {
                "type": (
                    "session.commentary.append" if spoken else "session.thinking.append"
                ),
                "content": content,
                "delegation_id": delegation_id,
                "event_id": _text(event_id, limit=512, label="client event ID"),
            }
        )

    async def receive(self) -> LiveEvent | None:
        """Read one event using a single host reader, even while closing."""
        event = await self._recv()
        kind = event["type"]
        if kind == "session.closed":
            session = event.get("session") or {}
            if session.get("id") != self.session_id:
                raise LiveProtocolError("Live close belongs to another session")
            self.ready = False
            self.closed = True
            self._finalized.set()
            return LiveClosed(
                str(event.get("reason") or "unknown"), dict(event.get("usage") or {})
            )
        if not self.ready:
            raise LiveProtocolError("Live event before session readiness")
        if self.closing:
            return None
        if kind == "session.output_audio.delta":
            try:
                pcm = base64.b64decode(event["delta"], validate=True)
            except (KeyError, ValueError, TypeError) as exc:
                raise LiveProtocolError("Invalid output PCM frame") from exc
            if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
                raise LiveProtocolError("Invalid output PCM frame")
            return LiveAudio(pcm)
        if kind in {
            "session.input_transcript.delta",
            "session.output_transcript.delta",
        }:
            identifier = _text(event.get("event_id"), limit=512, label="transcript ID")
            if identifier in self._transcripts:
                return None
            if len(self._transcripts) >= 65_536:
                raise LiveProtocolError("Live transcript budget exhausted")
            start, end = _time(event.get("start_ms")), _time(event.get("end_ms"))
            if start > end:
                raise LiveProtocolError("Invalid Live transcript range")
            self._transcripts.add(identifier)
            return LiveTranscript(
                "user" if kind == "session.input_transcript.delta" else "assistant",
                _text(event.get("delta"), limit=4096, label="transcript fragment"),
                start,
                end,
                identifier,
            )
        if kind == "session.delegation.created":
            delegation = event.get("delegation") or {}
            if (
                delegation.get("target") != "client"
                or delegation.get("type") != "delegation"
            ):
                raise LiveProtocolError("Unexpected Live delegation target")
            identifier = _text(delegation.get("id"), limit=256, label="delegation ID")
            if identifier in self._delegations:
                return None
            if len(self._delegations) >= 1024:
                raise LiveProtocolError("Live delegation budget exhausted")
            self._delegations.add(identifier)
            return LiveDelegation(
                identifier,
                _time(event.get("offset_ms")),
                _text(event.get("event_id"), limit=512, label="event ID"),
            )
        if kind in {"session.commentary.appended", "session.thinking.appended"}:
            return LiveAppendAccepted(
                _text(event.get("client_event_id"), limit=512, label="client event ID"),
                _time(event.get("end_ms")),
                kind == "session.commentary.appended",
            )
        return None

    async def close(self, *, timeout: float = 3) -> bool:
        """Request finalization once; bound transport cleanup even on failure."""
        async with self._close_lock:
            if self.closing:
                return self._finalized.is_set()
            self.closing = True
            try:
                if self.ready and not self.closed:
                    await self._send({"type": "session.close"})
                    try:
                        await asyncio.wait_for(self._finalized.wait(), timeout)
                    except TimeoutError:
                        pass
            finally:
                self.closed = True
                self.ready = False
                await self.socket.close()
            return self._finalized.is_set()
