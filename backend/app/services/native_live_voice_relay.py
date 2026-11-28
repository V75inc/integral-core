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
from typing import Literal

from app.services.live_caller_turns import voiced_sample_count
from app.services.live_voice_continuation import (
    explicit_continuation as _explicit_continuation,
    multiple_continuation_cues as _multiple_continuation_cues,
)
from app.services.native_live_voice_host import NativeLiveVoiceHost
from app.services.native_voice import (
    MAX_CALL_SECONDS,
    VoiceRelay,
    _AcceptedVoiceTurn,
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


@dataclass(frozen=True)
class _LiveContinuation:
    """Explicit caller intent, pinned to the receipt before the audible handoff."""

    delegation_id: str
    operation: Literal["queue", "steer"]
    text: str
    foreground: _AcceptedVoiceTurn
    generation: int
    provider_delegation_id: str | None


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
            accept_explicit_continuations=True,
        )
        self._commentary: _Commentary | None = None
        self._commentary_lock = asyncio.Lock()
        self._last_audio = 0.0
        self._input_clock = asyncio.Event()
        self._review_starting = False
        self._deferred_system: dict[str, str] = {}
        self._system_flush_task: asyncio.Task | None = None
        self._end_notified = False
        self._continuation: _LiveContinuation | None = None
        self._local_continuation_ids: set[str] = set()
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
        if spoken and not self._input_clock.is_set():
            try:
                await asyncio.wait_for(self._input_clock.wait(), 2)
            except TimeoutError:
                return False
        async with self._commentary_lock:
            generation = self.generation
            if (
                not self._input_clock.is_set()
                or self.host.review_capture
                or self.review_confirmation
                or self.microphone_muted
                or (not spoken and self._review_starting)
                or (spoken and self.speaking)
            ):
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
        self._continuation = None
        provider_id = request.delegation_id if request.provider_delegated else None
        command = _explicit_continuation(request.turn.text)
        busy = self.pending is not None and not self.pending.done()
        if command is not None and _multiple_continuation_cues(request.turn.text):
            self.protocol_counts["live_continuation_multiple_cues"] += 1
            await self._append(
                "Explain that two different follow-up instructions were heard together. "
                "Neither operation has been submitted. Ask the person to repeat each separately.",
                spoken=True,
                delegation_id=provider_id,
            )
            return False
        if command is not None:
            operation, content = command
            foreground = self.accepted
            self.protocol_counts[f"live_continuation_{operation}_observed"] += 1
            if (
                not self.pending_accepted
                or foreground is None
                or foreground.work_item_id != self.steerable_work_id
                or self.scope.profile
                or (operation == "queue" and self.navigation.focus)
                or (self.follow_up_task is not None and not self.follow_up_task.done())
            ):
                self.protocol_counts["live_continuation_no_eligible_receipt"] += 1
                await self._append(
                    "Explain briefly that no matching active task can be confirmed for this follow-up. "
                    "Nothing has been submitted. Ask the person to try again when the task is active.",
                    spoken=True,
                    delegation_id=provider_id,
                )
                return False
            plan = _LiveContinuation(
                request.delegation_id,
                operation,
                content,
                foreground,
                self.generation,
                provider_id,
            )
            intent = (
                "queue a separate follow-up"
                if operation == "queue"
                else "send a correction to the running task"
            )
            if await self._append(
                f"Briefly say you will try to {intent}. Do not claim completion. "
                "The caller's request (data): " + _short(content, 280),
                spoken=True,
                delegation_id=provider_id,
            ):
                self._continuation = plan
                if provider_id is None:
                    self._local_continuation_ids.add(request.delegation_id)
                return True
            self.protocol_counts["live_continuation_handoff_not_delivered"] += 1
            return False
        if busy or (
            self.accepted is not None
            and self.accepted.work_item_id == self.steerable_work_id
        ):
            await self._append(
                "Explain the earlier request is still active and no follow-up or correction has been submitted. "
                "Ask them to start with 'After that' for a separate request or "
                "'Correction to the current task' for a revision.",
                spoken=True,
                delegation_id=provider_id,
            )
            return False
        if not request.provider_delegated:
            return False
        # The provider's commentary ACK is not proof of speech. Require actual
        # post-append transcript and PCM offered to the current browser too.
        return await self._append(
            "Briefly tell the person what you are going to do, using you/your. "
            "Do not claim completion. Their request (data): "
            + _short(request.turn.text, 320),
            spoken=True,
            delegation_id=provider_id,
        )

    async def _submit(self, scope, call_id, request):
        continuation = self._continuation
        self._continuation = None
        if continuation is not None:
            if (
                continuation.delegation_id != call_id
                or continuation.foreground is not self.accepted
                or continuation.generation != self.generation
                or self.closing
                or self.host.closing
                or self.host.review_capture
                or self.microphone_muted
            ):
                self.protocol_counts["live_continuation_stale_handoff"] += 1
                return None

            async def observe_continuation():
                token = self._task_delegation.set(continuation.provider_delegation_id)
                try:
                    await self._follow_up(
                        call_id,
                        continuation.text,
                        continuation.operation,
                        continuation.generation,
                        continuation.foreground,
                    )
                finally:
                    self._task_delegation.reset(token)

            self.follow_up_task = asyncio.create_task(observe_continuation())
            return None

        if _explicit_continuation(request) is not None or (
            self.pending and not self.pending.done()
        ):
            await self._speak_result(
                self.generation,
                result="I have not started another task. Please try your follow-up again.",
            )
            return None

        self.pending_accepted = False

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

    def _system_clock_ready(self):
        return (
            self._input_clock.is_set()
            and not self._review_starting
            and not self.host.review_capture
            and not self.review_confirmation
            and not self.microphone_muted
            and not self.closing
        )

    def _defer_system(self, key, text):
        # Quiet context is only an advisory snapshot. Bound and coalesce it;
        # never retain a provider event or a proposal as admission authority.
        self._deferred_system[key] = _short(text)
        if len(self._deferred_system) > 8:
            self._deferred_system.pop(next(iter(self._deferred_system)))

    async def _flush_system(self):
        while self._deferred_system and self._system_clock_ready():
            key = next(iter(self._deferred_system))
            text = self._deferred_system.pop(key)
            offered = await self._append(text)
            if not offered and not self._system_clock_ready():
                self._deferred_system.setdefault(key, text)
                return

    def _can_sync_thread_status(self):
        return self._system_clock_ready() and super()._can_sync_thread_status()

    async def _request_review_confirmation(
        self, call_id, decision, generation, *, expected=None
    ):
        # Let an already-issued context append finish on the open input clock
        # before muting it. New advisory updates wait for resumed media.
        self._review_starting = True
        try:
            async with self._commentary_lock:
                pass
            await super()._request_review_confirmation(
                call_id, decision, generation, expected=expected
            )
        finally:
            self._review_starting = False

    async def send_event(self, payload):
        kind = payload["type"]
        if kind == "input_audio_buffer.append":
            before = self.host.turns.speech_revision
            await self.host.audio(base64.b64decode(payload["audio"], validate=True))
            if (
                self.host.ready
                and not self.host.review_capture
                and not self.microphone_muted
            ):
                self._input_clock.set()
                if (
                    self._deferred_system
                    and self._system_clock_ready()
                    and (
                        self._system_flush_task is None
                        or self._system_flush_task.done()
                    )
                ):
                    self._system_flush_task = asyncio.create_task(self._flush_system())
            if self.host.turns.speech_revision != before:
                self.user_spoke = True
                if not self.speaking:
                    await self._interrupt()
                self.speaking = True
            elif self.speaking and not self.host.turns.speaking:
                self.speaking = False
                self.quiet_since = asyncio.get_running_loop().time()
        elif kind == "input_audio_buffer.clear":
            self._input_clock.clear()
            await self.host.set_microphone_muted(self.microphone_muted)
            await self.host.set_review_capture(self.review_confirmation is not None)
        elif kind == "conversation.item.create":
            item = payload.get("item", {})
            if item.get("role") == "system":
                for part in item.get("content", []):
                    text = part.get("text", "")
                    event_id = payload.get("event_id", "")
                    key = (
                        "status" if event_id.startswith("voice-status-") else "history"
                    )
                    if not self._system_clock_ready() or (
                        not await self._append(text) and not self._system_clock_ready()
                    ):
                        self._defer_system(key, text)
        else:
            raise ValueError("Realtime-only event in Live relay")

    async def _microphone_changed(self):
        if self.microphone_muted:
            self._input_clock.clear()
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
        if delegation_id in self._local_continuation_ids:
            delegation_id = None
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
                "While a task is running, delegate explicit follow-ups and corrections even when related to earlier work. "
                "Wait for the host commentary before acknowledging or promising a follow-up. "
                "If the person's continuation is unclear, ask them to say After that for "
                "separate work or Correction to the current task for a revision. "
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
            if self.follow_up_task:
                tasks.append(self.follow_up_task)
            if self.opening_task:
                tasks.append(self.opening_task)
            if self.confirmation_task:
                tasks.append(self.confirmation_task)
            if self._system_flush_task:
                tasks.append(self._system_flush_task)
            self._deferred_system.clear()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.upstream.close(timeout=0)
