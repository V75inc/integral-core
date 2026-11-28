"""Conservative local PCM turn fences for GPT-Live client delegation.

Provider transcript timing supplies ordering; PCM supplies a separate speech
boundary. No transcript delta, delegation notice or silence frame alone can
admit work. Review decisions still require the separate confirmation capture.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable
from uuid import uuid4

from app.services.openai_voice_protocol import (
    PCM_RATE,
    CompletedCallerTurn,
    LiveDelegation,
    LiveTranscript,
)

_QUIET_SAMPLES = PCM_RATE * 3 // 2
_MIN_VOICED_SAMPLES = PCM_RATE // 12
_SETTLE_SECONDS = 0.5
_MAX_SECONDS = 90


def voiced_sample_count(pcm: bytes) -> int:
    """Conservative PCM energy hint, never a semantic or playback receipt."""
    samples = array("h", pcm)
    voiced = sum(abs(value) > 700 for value in samples)
    return voiced if voiced > len(samples) // 10 else 0


@dataclass
class LiveCallerTurns:
    """Pair complete local speech with actual timestamped Live transcript fragments."""

    clock: Callable[[], float] = monotonic
    _voiced: int = 0
    _quiet: int = 0
    _samples: int = 0
    _last_change: float = 0
    _previous_end_ms: int = -1
    _last_transcript_at: float = 0
    _fragments: list[LiveTranscript] = field(default_factory=list)
    _delegations: list[LiveDelegation] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    _invalid: bool = False
    speech_revision: int = 0

    @property
    def speaking(self) -> bool:
        """Whether local voiced audio lacks the required quiet tail."""
        return self._voiced > 0 and self._quiet < _QUIET_SAMPLES

    def audio(self, pcm: bytes) -> None:
        """Local evidence of speech and a sustained quiet tail, not transcription."""
        if not pcm or len(pcm) % 2:
            self._invalid = True
            return
        count = len(pcm) // 2
        voiced = voiced_sample_count(pcm)
        if voiced:
            self.speech_revision += 1
            self._voiced += voiced
            self._quiet = 0
            self._last_change = self.clock()
        elif self._voiced:
            self._quiet += count
        if self._voiced:
            self._samples += count
            if self._samples > PCM_RATE * _MAX_SECONDS:
                self._invalid = True

    def observe(self, event: LiveTranscript | LiveDelegation) -> None:
        if isinstance(event, LiveTranscript):
            if event.speaker != "user" or event.end_ms <= self._previous_end_ms:
                return
            if event.event_id in self._seen:
                return
            if len(self._fragments) >= 1024 or len(self._seen) >= 65_536:
                self._invalid = True
                return
            self._seen.add(event.event_id)
            self._fragments.append(event)
            self._last_transcript_at = self.clock()
        elif event.offset_ms > self._previous_end_ms:
            if any(item.identifier == event.identifier for item in self._delegations):
                return
            if len(self._delegations) >= 64:
                self._invalid = True
                return
            self._delegations.append(event)

    def discard(self) -> None:
        """Never carry inhibited, muted, interrupted review or invalid speech forward."""
        self._previous_end_ms = max(
            [self._previous_end_ms]
            + [item.end_ms for item in self._fragments]
            + [item.offset_ms for item in self._delegations]
        )
        self._voiced = self._quiet = self._samples = 0
        self._last_change = self._last_transcript_at = 0
        self._fragments.clear()
        self._delegations.clear()
        self._invalid = False

    def poll(self) -> CompletedCallerTurn | None:
        """Return one settled utterance after PCM silence plus transcript quiet.

        A provider delegation can precede a correction in the same spoken turn.
        When it arrives later, it must still be near the current transcript
        boundary. A late event from an already committed turn is ignored.
        """
        if self._invalid:
            self.discard()
            return None
        if (
            self._voiced < _MIN_VOICED_SAMPLES
            or self._quiet < _QUIET_SAMPLES
            or not self._fragments
            or self.clock() - max(self._last_change, self._last_transcript_at)
            < _SETTLE_SECONDS
        ):
            return None
        start = min(item.start_ms for item in self._fragments)
        end = max(item.end_ms for item in self._fragments)
        # Allow the provider's delegation to follow the last spoken word, but
        # never bind a delegation from before this utterance or far afterward.
        bound = [
            item for item in self._delegations if start <= item.offset_ms <= end + 3000
        ]
        if not bound and self.clock() - self._last_transcript_at < 3:
            return None
        text = "".join(item.delta for item in self._fragments).strip()
        if not text or len(text) > 16_000 or end <= start:
            self.discard()
            return None
        end = max([end] + [item.offset_ms for item in bound])
        turn = CompletedCallerTurn(uuid4().hex, start, end, text)
        self.discard()
        self._previous_end_ms = max(self._previous_end_ms, end)
        return turn
