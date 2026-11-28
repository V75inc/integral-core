"""GPT-Live application-side admission seam for the native voice conversation.

The protocol adapter never executes a model delegation. This host binds it to
the same authorized conversation and durable resident submission as Realtime.
The media relay supplies independently completed caller turns and audible
handoff evidence; neither can be inferred from a Live append acknowledgment.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from app.services.live_caller_turns import LiveCallerTurns
from app.services.live_voice_continuation import explicit_continuation
from app.services.native_voice import (
    VoiceScope,
    _bind_ready_thread,
    _explicit_end_call,
    authorize,
    delegate,
)
from app.services.openai_voice_protocol import (
    CompletedCallerTurn,
    LiveAudio,
    LiveClosed,
    LiveDelegation,
    LiveEvent,
    LiveTaskRequest,
    LiveTranscript,
    LiveTurnLedger,
    OpenAILiveSession,
)


class NativeLiveVoiceHost:
    """Shared application-side authority, with explicit Live observation fences."""

    def __init__(
        self,
        session: OpenAILiveSession,
        scope: VoiceScope,
        *,
        acknowledge: Callable[[LiveTaskRequest], Awaitable[bool]],
        check=authorize,
        submit=delegate,
        bind_thread=_bind_ready_thread,
        accept_explicit_continuations: bool = False,
        request_end: Callable[[], Awaitable[None]] | None = None,
    ):
        self.session = session
        self.scope = scope
        self.check = check
        self.submit = submit
        self.bind_thread = bind_thread
        self.acknowledge = acknowledge
        self.accept_explicit_continuations = accept_explicit_continuations
        self.request_end = request_end
        self.ledger = LiveTurnLedger()
        self.turns = LiveCallerTurns()
        self.ready = False
        self.closing = False
        self.microphone_muted = False
        self.review_capture = False

    async def start(self, *, instructions: str, voice: str) -> None:
        """Bind a thread only after the selected provider accepts the session."""
        await self.check(self.scope)
        await self.session.start(instructions=instructions, voice=voice)
        try:
            await self.bind_thread(self.scope, self.check)
        except BaseException:
            await self.session.close(timeout=0)
            raise
        self.ready = True

    async def audio(self, pcm: bytes) -> None:
        """Forward ordinary microphone audio, never independent review capture."""
        if (
            not self.ready
            or self.closing
            or self.microphone_muted
            or self.review_capture
        ):
            return
        await self.check(self.scope)
        self.turns.audio(pcm)
        await self.session.append_audio(pcm)

    async def observe(self, event: LiveEvent | None) -> bytes | None:
        """Return authorized PCM and retain observations without admitting work."""
        if isinstance(event, LiveClosed):
            self.closing = True
            self.ready = False
            return None
        if not self.ready or self.closing:
            return None
        await self.check(self.scope)
        if isinstance(event, LiveAudio):
            # Exact review speech uses the separate verified playback path.
            return None if self.review_capture else event.pcm
        if isinstance(event, (LiveTranscript, LiveDelegation)):
            if not self.review_capture and not self.microphone_muted:
                self.ledger.observe(event)
                self.turns.observe(event)
        return None

    async def set_microphone_muted(self, muted: bool) -> None:
        """Invalidate uncommitted utterances and mute the upstream session."""
        self.microphone_muted = muted
        if muted:
            self.turns.discard()
        if self.ready and not self.closing:
            await self.session.mute(muted or self.review_capture)

    async def set_review_capture(self, active: bool) -> None:
        """Separate exact review capture from general provider audio."""
        self.review_capture = active
        self.turns.discard()
        if self.ready and not self.closing:
            await self.session.mute(active or self.microphone_muted)

    async def poll_completed_turn(self) -> str | None:
        """Admit only a settled PCM/transcript turn, independent of provider ACKs."""
        turn = self.turns.poll()
        return None if turn is None else await self.commit_completed_turn(turn)

    async def commit_completed_turn(self, turn: CompletedCallerTurn) -> str | None:
        """Submit once through the native resident, after real handoff evidence.

        The separate review path owns affirmative/negative decisions. A Live
        transcript or a delegation event never supplies that proof.
        """
        request = self.ledger.commit(
            turn,
            accept_explicit_continuation=(
                self.accept_explicit_continuations
                and not turn.superseded
                and explicit_continuation(turn.text) is not None
                and not self.review_capture
                and not self.microphone_muted
            ),
        )
        if (
            not self.ready
            or self.closing
            or self.microphone_muted
            or self.review_capture
            or turn.superseded
        ):
            return None
        await self.check(self.scope)
        if self.scope.actions.end_call and _explicit_end_call(turn.text):
            if self.request_end is not None:
                # The relay must announce call_ending before finalization and
                # own the browser terminal event, including provider timeout.
                await self.request_end()
            else:
                self.closing = True
                self.ready = False
                await self.session.close()
            return None
        speech_revision = self.turns.speech_revision
        if request is None or not await self.acknowledge(request):
            return None
        # Recheck after the caller hears the spoken intent. A disconnect or
        # a newly pending confirmation cannot become a second work submission.
        if (
            self.closing
            or self.microphone_muted
            or self.review_capture
            or self.turns.speech_revision != speech_revision
        ):
            return None
        await self.check(self.scope)
        result = await self.submit(self.scope, request.delegation_id, request.turn.text)
        if not self.ready or self.closing:
            return None
        await self.check(self.scope)
        return result
