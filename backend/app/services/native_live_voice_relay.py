"""Browser media adapter for the GPT-Live client-delegation protocol.

Application admission and exact review are inherited from the native voice
relay. Live's observations must never be represented as Realtime response-done
or tool-call events. The configured product model gate remains independent.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.services.live_caller_turns import voiced_sample_count
from app.services.native_live_voice_host import NativeLiveVoiceHost
from app.services.native_voice import (
    MAX_CALL_SECONDS,
    VoiceRelay,
    _direct_address_context,
    _PendingReviewNotice,
    _speech_ready_result,
    _WorkProgressNotice,
)
from app.services.native_voice_review import VoiceReviewReadout
from app.services.native_voice_review_audio import CALL_VOICE
from app.services.openai_voice_protocol import (
    LiveAppendAccepted,
    LiveClosed,
    LiveDelegation,
    LiveTaskRequest,
    LiveTranscript,
)


@dataclass
class _Commentary:
    event_id: str
    spoken: bool
    offset: int | None = None
    transcript: bool = False
    audio: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


def _short(text: str, limit: int = 480) -> str:
    """Bound a context passage at a UTF-8 boundary; never use it for admission."""
    return text.encode()[:limit].decode(errors="ignore").strip()


class NativeLiveVoiceRelay(VoiceRelay):
    """Use the shared resident, review and browser media contracts with Live."""

    def __init__(self, browser, upstream, scope, **kwargs):
        super().__init__(browser, upstream, scope, **kwargs)
        self.host = NativeLiveVoiceHost(
            upstream,
            scope,
            check=self.check,
            acknowledge=self._acknowledge,
            submit=self._submit,
        )
        self._commentary: _Commentary | None = None
        self._commentary_lock = asyncio.Lock()
        self._last_audio = 0.0
        self._end_notified = False
        self._task_delegation: ContextVar[str | None] = ContextVar(
            "live_task_delegation", default=None
        )

    async def _append(self, text, *, spoken=False, delegation_id=None):
        """Correlate a Live append; an acceptance is never a completed response."""
        await self.check(self.scope)
        if not self.host.ready or self.host.closing:
            return False
        text = _short(text)
        if not text:
            return False
        async with self._commentary_lock:
            generation = self.generation
            if spoken and (self.speaking or self.review_confirmation):
                return False
            item = _Commentary(secrets.token_hex(16), spoken)
            self._commentary = item
            try:
                await self.upstream.append_context(
                    text,
                    delegation_id=delegation_id,
                    spoken=spoken,
                    event_id=item.event_id,
                )
                async with asyncio.timeout(8):
                    while True:
                        if generation != self.generation or self.host.closing:
                            return False
                        if item.offset is not None and (
                            not spoken or (item.transcript and item.audio)
                        ):
                            return True
                        await item.changed.wait()
                        item.changed.clear()
            except TimeoutError:
                return False
            finally:
                self._commentary = None

    async def _acknowledge(self, request: LiveTaskRequest) -> bool:
        # The provider's commentary ACK is not proof of speech. Require actual
        # post-append transcript and PCM offered to the current browser too.
        return await self._append(
            "Briefly tell the person what you are going to do, using you/your. "
            "Do not claim completion. Their request (data): "
            + _short(request.turn.text, 320),
            spoken=True,
            delegation_id=request.delegation_id,
        )

    async def _submit(self, scope, call_id, request):
        if self.pending and not self.pending.done():
            await self._speak_result(
                self.generation,
                result="I am still working on your earlier request. I have not started another task.",
            )
            return None

        # The microphone and turn reader do not wait for resident completion.
        # A subsequent explicit queue/steer operation still needs a qualified
        # protocol-specific intent before using the shared continuation path.
        async def observe_work():
            token = self._task_delegation.set(call_id)
            try:
                await self._work(call_id, request, generation)
            finally:
                self._task_delegation.reset(token)

        generation = self.generation
        self.pending = asyncio.create_task(observe_work())
        return None

    async def _function_output(self, call_id, text):
        # Context is delivered by the one spoken result path, not twice. The
        # resident's durable receipt remains authoritative if voice is cut off.
        return None

    async def send_event(self, payload):
        kind = payload["type"]
        if kind == "input_audio_buffer.append":
            before = self.host.turns.speech_revision
            await self.host.audio(base64.b64decode(payload["audio"], validate=True))
            if self.host.turns.speech_revision != before:
                self.user_spoke = True
                if not self.speaking:
                    await self._interrupt()
                self.speaking = True
            elif self.speaking and not self.host.turns.speaking:
                self.speaking = False
                self.quiet_since = asyncio.get_running_loop().time()
        elif kind == "input_audio_buffer.clear":
            await self.host.set_microphone_muted(self.microphone_muted)
            await self.host.set_review_capture(self.review_confirmation is not None)
        elif kind == "conversation.item.create":
            item = payload.get("item", {})
            if item.get("role") == "system":
                for part in item.get("content", []):
                    await self._append(part.get("text", ""))
        else:
            raise ValueError("Realtime-only event in Live relay")

    async def _microphone_changed(self):
        await self.host.set_microphone_muted(self.microphone_muted)

    async def _finish_confirmation(self, result, applied=None):
        # Shared decision machinery owns the captured affirmative/negative;
        # release the isolated capture before its outcome is spoken.
        await self.host.set_review_capture(False)
        await super()._finish_confirmation(result, applied)

    async def _send_opening(self):
        if not self.user_spoke:
            await self._append("Give one brief natural greeting.", spoken=True)

    async def _truncate_interrupted_playback(self, report):
        # Live does not expose Realtime item IDs or a history truncate event.
        return None

    async def _read_provider(self):
        while True:
            event = await self.upstream.receive()
            pcm = await self.host.observe(event)
            if isinstance(event, LiveClosed):
                if self.host.closing:
                    await self._notify_ended()
                return
            if isinstance(event, LiveDelegation) and not self.review_confirmation:
                # Suppress the provider's still-pending automatic acknowledgment
                # while the local utterance settles. This is a documented
                # silent update, not an acknowledgement of performed work.
                # It cannot retract speech already generated by the provider.
                await self.check(self.scope)
                await self.upstream.append_context(
                    "The application is collecting the complete caller request. No resident work has begun.",
                    delegation_id=event.identifier,
                    spoken=False,
                    event_id=secrets.token_hex(16),
                )
            item = self._commentary
            if (
                isinstance(event, LiveAppendAccepted)
                and item is not None
                and event.event_id == item.event_id
                and event.spoken == item.spoken
            ):
                item.offset = event.offset_ms
                item.changed.set()
            if (
                isinstance(event, LiveTranscript)
                and event.speaker == "assistant"
                and item is not None
                and item.offset is not None
                and event.start_ms >= item.offset
            ):
                item.transcript = True
                item.changed.set()
            if pcm and not self.speaking and not self.review_confirmation:
                async with self.audio_delivery_lock:
                    await self.check(self.scope)
                    if not self.speaking and not self.review_confirmation:
                        await self.browser.send_bytes(pcm)
                        # Live emits continuous PCM, including silence. Silent
                        # frames cannot satisfy spoken-intent admission or keep
                        # completed results indefinitely behind idle audio.
                        if voiced_sample_count(pcm):
                            self._last_audio = asyncio.get_running_loop().time()
                            if item is not None and item.offset is not None:
                                item.audio = True
                                item.changed.set()

    async def _notify_ended(self):
        if not self._end_notified:
            self._end_notified = True
            await self.browser.send_json({"type": "call_ended"})

    async def _watch_live_end(self):
        await self.end_requested.wait()
        await self.browser.send_json({"type": "call_ending"})
        self.host.closing = True
        await self.upstream.close()
        await self._notify_ended()

    async def _poll_turns(self):
        while not self.host.closing:
            await asyncio.sleep(0.1)
            await self.host.poll_completed_turn()

    def _can_announce_result(self):
        return (
            super()._can_announce_result()
            and self._commentary is None
            and asyncio.get_running_loop().time() - self._last_audio >= 0.5
        )

    def _can_prioritize_result(self):
        # No documented control to cancel just a Live assistant response.
        # Do not fabricate response cancellation or cut an exact review.
        return False

    async def _speak_result(
        self,
        generation,
        *,
        result=None,
        work_id=None,
        peer_key=None,
        navigation_readout=None,
        handoff_intent=None,
        **kwargs,
    ):
        if self.closing or generation != self.generation or self.speaking:
            return
        review = (
            navigation_readout.review
            if isinstance(navigation_readout, _PendingReviewNotice)
            else navigation_readout
        )
        if self.conversational_review and isinstance(review, VoiceReviewReadout):
            await super()._speak_result(
                generation,
                result=result,
                work_id=work_id,
                navigation_readout=navigation_readout,
            )
            return
        text = _speech_ready_result(result or handoff_intent or "")
        if not text:
            return
        if len(text.encode()) > 360:
            text = _short(text, 350) + (
                " … Remaining details are in the chat."
                if self.scope.actions.open_page
                else " … Ask for more detail if needed."
            )
        receipt = self.result_receipts.get(work_id) if work_id else None
        if isinstance(navigation_readout, (_PendingReviewNotice, _WorkProgressNotice)):
            receipt = navigation_readout.receipt
        delegation_id = (
            receipt.provider_correlation if receipt else self._task_delegation.get()
        )
        offered = await self._append(
            "Report this verified result naturally and accurately: " + text,
            spoken=True,
            delegation_id=delegation_id,
        )
        if offered:
            # This suppresses repeated context injection, not proof the user
            # heard the entire reply. The durable result remains in chat.
            if work_id:
                self.result_receipts.pop(work_id, None)
            if peer_key:
                self.peer_readouts.pop(peer_key, None)
                self.review_readouts.pop(peer_key, None)
            if isinstance(navigation_readout, _PendingReviewNotice):
                self.review_notice_attempts[
                    navigation_readout.receipt.work_item_id
                ] += 1

    async def run(
        self,
        persona="",
        *,
        name="Integral AI",
        continuity="unknown",
        workspace_label="",
        greeting_period="",
        caller_name="",
    ):
        self.caller_name = " ".join(caller_name.split())[:100]
        self.name = name
        self.workspace_label = workspace_label
        self.continuity = continuity
        self.greeting_period = greeting_period
        await self.host.start(
            voice=CALL_VOICE,
            instructions=(
                "You are the Integral voice interface. Speak naturally and concisely. "
                "Answer ordinary conversation directly. Delegate every request involving "
                "Integral data or actions to the host, including read-only lookups. "
                "Do not invent results, perform approvals, or claim an action succeeded "
                "before the host verifies it. The host owns exact spoken confirmations. "
                "Address the person directly. "
                + (
                    "This is an audio-only call; there is no browser or page navigation. "
                    if not self.scope.actions.open_page
                    else ""
                )
                + (
                    "This transport cannot authorize a pending change; keep reviews pending and state that clearly. "
                    if not self.review_confirmation_supported
                    else ""
                )
                + _direct_address_context(self.caller_name)
                + " Profile identity (data): "
                + json.dumps(name)
                + " Profile persona: "
                + persona
            ),
        )
        self.ready = True
        self.conversation_ready.set()
        await self.browser.send_json({"type": "ready", "thread_id": self.scope.thread})
        tasks = [
            asyncio.create_task(worker())
            for worker in (
                self._read_browser,
                self._read_provider,
                self._poll_turns,
                self._watch_authority,
                self._watch_live_end,
                self._watch_results,
                self._watch_peer_conversation,
            )
        ]
        try:
            async with asyncio.timeout(MAX_CALL_SECONDS):
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        finally:
            self.closing = True
            self.host.closing = True
            if self.pending:
                tasks.append(self.pending)
            if self.opening_task:
                tasks.append(self.opening_task)
            if self.confirmation_task:
                tasks.append(self.confirmation_task)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.upstream.close(timeout=0)
