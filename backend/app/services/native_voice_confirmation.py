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
# A brief "no" can have far less voiced audio than a six-word answer.
MIN_VOICED_SAMPLES = SAMPLE_RATE // 16


@dataclass
class VoiceConfirmation:
    review: VoiceReviewReadout = field(repr=False)
    speech_binding: tuple[str, str, str, str] = field(repr=False)
    identifier: str = field(default_factory=lambda: secrets.token_hex(16), repr=False)
    expires: float = field(default_factory=lambda: monotonic() + 120)
    phase: str = "synthesizing"
    heard_decision: str | None = field(default=None, init=False)
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
            self.voiced_samples >= MIN_VOICED_SAMPLES
            and self.silence_samples >= SAMPLE_RATE
            and len(self.audio) >= SAMPLE_RATE * 2
        ):
            self.capture_done.set()

    def wav(self) -> bytes | None:
        samples = array("h", self.audio)
        # Reject silence or a short transient. No transcription request for it.
        if (
            len(samples) < SAMPLE_RATE
            or sum(abs(x) > 700 for x in samples) < MIN_VOICED_SAMPLES
        ):
            return None
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(self.audio)
        return stream.getvalue()


def spoken_review_choice(transcript: str) -> str | None:
    """Interpret a complete English reply to the exact change just played."""
    if not isinstance(transcript, str) or len(transcript) > 180:
        return None
    normalized = transcript.casefold().strip().replace("’", "'")
    # Preserve qualifications, numbers and question marks. Finding a 'yes'
    # somewhere in a longer utterance is insufficient for a decision.
    words = " ".join(re.sub(r"[,.!]+", " ", normalized).split())
    if not words:
        return None
    target = r"(?: (?:it|that|this|both|the (?:update|change|proposal|request)))?"
    positive = (
        r"yes|yeah|yep|yup|absolutely|definitely|certainly|of course|you bet|"
        r"by all means|no problem|affirmative|sure(?: thing)?|ok(?:ay)?|"
        r"(?:sounds|looks) good(?: to me)?|that(?:'s| is) (?:fine|right|correct)|"
        r"that works(?: for me)?|i would(?: like (?:that|you to))?|i agree|"
        r"go (?:ahead|for it)|proceed|(?:let's )?do (?:it|that)|please do|"
        rf"(?:i )?(?:approve|accept|confirm){target}"
    )
    negative = (
        r"no|nope|nah|negative|no thank you|no thanks|not this time|"
        r"i(?:'d| would) rather not|i(?:'d| would) prefer not to|"
        rf"(?:i )?(?:reject|decline|cancel){target}|"
        rf"(?:i )?(?:do not|don't) (?:go ahead|proceed|do (?:it|that)|approve{target})|"
        r"leave it (?:alone|as is)|skip (?:it|that)|never mind"
    )
    # A standalone refusal can end a reply. Consuming "don't" as a prefix
    # would misclassify "don't reject it" as a rejection.
    if re.fullmatch(r"(?:no )?(?:please )?(?:i )?(?:do not|don't)", words):
        return "reject"
    for decision, alternatives in (("approve", positive), ("reject", negative)):
        unit = f"(?:{alternatives})"
        # Permit polite and repeated agreement of the same polarity only.
        if re.fullmatch(
            rf"(?:please )?{unit}(?: (?:and )?(?:please )?{unit})*(?: (?:please|thanks|thank you))?",
            words,
        ):
            return decision
    return None
