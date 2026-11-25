"""A separately captured human confirmation, independent of realtime tool output."""

import asyncio
import io
import re
import secrets
import wave
from array import array
from dataclasses import dataclass, field
from time import monotonic

from app.services.native_voice_review import VoiceReviewReadout

CAPTURE_SECONDS = 7
SAMPLE_RATE = 24_000
MAX_CAPTURE_BYTES = CAPTURE_SECONDS * SAMPLE_RATE * 2
DIGITS = dict(
    zip(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
        ],
        "0123456789",
    )
)


@dataclass
class VoiceConfirmation:
    review: VoiceReviewReadout = field(repr=False)
    decision: str
    speech_binding: tuple[str, str, str, str] = field(repr=False)
    identifier: str = field(default_factory=lambda: secrets.token_hex(16), repr=False)
    code: str = field(
        default_factory=lambda: "".join(secrets.choice("0123456789") for _ in range(6)),
        repr=False,
    )
    expires: float = field(default_factory=lambda: monotonic() + 120)
    phase: str = "prompt"
    host_fallback_used: bool = False
    capture_after: float | None = None
    audio: bytearray = field(default_factory=bytearray, repr=False)
    capture_done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    voiced_samples: int = 0
    silence_samples: int = 0

    def append_audio(self, frame: bytes) -> None:
        """Stop after a sustained quiet tail; keep the existing hard limit."""
        remaining = MAX_CAPTURE_BYTES - len(self.audio)
        if remaining <= 0 or self.capture_done.is_set():
            return
        frame = frame[:remaining]
        self.audio.extend(frame)
        samples = array("h", frame)
        voiced = sum(abs(sample) > 700 for sample in samples)
        if voiced > len(samples) // 10:
            self.voiced_samples += voiced
            self.silence_samples = 0
        else:
            self.silence_samples += len(samples)
        if len(self.audio) >= MAX_CAPTURE_BYTES or (
            self.voiced_samples >= SAMPLE_RATE // 5
            and self.silence_samples >= SAMPLE_RATE
            and len(self.audio) >= SAMPLE_RATE * 2
        ):
            self.capture_done.set()

    @property
    def prompt(self):
        digits = ", ".join(self.code)
        return (
            self.review.text
            + f" Voice confirmation. To {self.decision} this exact change, after a short pause, "
            f"say “I {self.decision}” followed by these six digits: {digits}. "
            "Anything else leaves the proposal unchanged."
        )

    def matches(self, transcript: str) -> bool:
        # Accept only a complete independently transcribed phrase. Do not
        # extract a substring, repair a missing digit or infer from 'yes'.
        if not isinstance(transcript, str) or len(transcript) > 100:
            return False
        phrase = transcript.strip().lower().rstrip(".!")
        match = re.fullmatch(
            rf"(?:i +)?{re.escape(self.decision)}[ .,:;-]+(.+)", phrase
        )
        if not match:
            return False
        words = re.split(r"[ ,:;-]+", match[1])
        digits = "".join(DIGITS.get(word, word) for word in words)
        return bool(
            re.fullmatch(r"[0-9]{6}", digits)
            and secrets.compare_digest(digits, self.code)
        )

    def wav(self) -> bytes | None:
        samples = array("h", self.audio)
        # Reject silence or a short transient. No transcription request for it.
        if (
            len(samples) < SAMPLE_RATE
            or sum(abs(x) > 700 for x in samples) < SAMPLE_RATE // 5
        ):
            return None
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(self.audio)
        return stream.getvalue()
