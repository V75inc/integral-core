"""Bridge durable steering inputs into Pydantic's native pending-message queue."""

from collections.abc import Callable

from app.agentive.harness.pydantic_ai_compat import (
    AbstractCapability,
    Agent,
    End,
    EnqueuedMessagesEvent,
    ModelRequest,
    UserPromptPart,
)
from app.schemas.agentive.work import WorkExecutionContext
from app.services.chat_steering import load_steering_corrections
from app.services.chat_turn_events import append_work_item_chat_event


class DurableSteering(AbstractCapability):
    def __init__(
        self,
        context: WorkExecutionContext,
        settles: Callable[[object], bool] | None = None,
    ) -> None:
        self.context = context
        self.settles = settles
        self.enqueued: dict[str, str] = {}

    async def on_event(self, ctx, *, event):
        if not isinstance(event, EnqueuedMessagesEvent):
            return
        correction_id = self.enqueued.get(event.enqueue_id)
        if correction_id is not None:
            await append_work_item_chat_event(
                context=self.context,
                event_key=f"steering-delivered:{correction_id}",
                event={"type": "steering-delivered", "correction_id": correction_id},
            )

    async def wrap_node_run(self, ctx, *, node, handler):
        if Agent.is_model_request_node(node):
            await self._enqueue_pending(ctx)
        result = await handler(node)
        # Pydantic drains the queue again when a normal text run would end.
        # Deferred approval output must still settle as an approval boundary.
        if isinstance(result, End):
            output = result.data.output
            settles = self.settles(output) if self.settles else isinstance(output, str)
            if settles:
                await self._enqueue_pending(ctx, close_if_drained=True)
        return result

    async def _enqueue_pending(self, ctx, *, close_if_drained=False):
        seen = set(self.enqueued.values()) | {
            marker[1]
            for message in ctx.messages
            if isinstance(message, ModelRequest)
            and isinstance(
                marker := (message.metadata or {}).get("integral_steering"), list
            )
            and len(marker) == 2
            and marker[0] == self.context.work_item_id
            and isinstance(marker[1], str)
        }
        for correction_id, text in (
            await load_steering_corrections(
                self.context, seen, close_if_drained=close_if_drained
            )
        ).items():
            enqueue_id = ctx.enqueue(
                ModelRequest(
                    parts=[UserPromptPart(text)],
                    metadata={
                        "integral_steering": [
                            self.context.work_item_id,
                            correction_id,
                        ]
                    },
                ),
                priority="asap",
            )
            if enqueue_id is not None:
                self.enqueued[enqueue_id] = correction_id
