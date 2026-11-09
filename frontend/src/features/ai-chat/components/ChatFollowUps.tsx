import { useChatActivity } from "../AIChatSurface";
import { Text } from "../../../ui";

export function ChatFollowUps({ locked }: { locked: boolean }) {
  const {
    followUps, steeringRunning, steeringWorkItemId,
    steerFollowUp, removeFollowUp,
  } = useChatActivity();
  if (!followUps?.length) return null;

  return (
    <ul aria-label="Follow-up messages" className="flex min-w-0 flex-col gap-2 px-2">
      {followUps.map((item) => {
        const canSteer = !locked && steeringRunning && Boolean(steeringWorkItemId) &&
          (!item.workItemId || item.workItemId === steeringWorkItemId) &&
          (item.state === "queued" || item.state === "unconfirmed");
        const status = {
          queued: "Queued for the next turn",
          sending: "Sending correction…",
          accepted: item.workItemId === steeringWorkItemId && steeringRunning
            ? "Accepted · Waiting for delivery" : "Accepted · Delivery unconfirmed",
          unconfirmed: "Couldn’t confirm steering",
        }[item.state];
        return (
          <li key={item.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--radius-input)] border border-[var(--border-subtle)] px-3 py-2">
            <div className="min-w-0 flex-1 break-words">
              <div role="status"><Text as="p" variant="meta" tone="subtle">{status}</Text></div>
              <Text as="p" variant="meta">{item.text}</Text>
            </div>
            {canSteer && (
              <button type="button" className="underline" onClick={() => void steerFollowUp?.(item.id)}>
                <Text as="span" variant="meta">{item.state === "unconfirmed" ? "Retry steering" : "Steer now"}</Text>
              </button>
            )}
            {item.state !== "sending" && (
              <button type="button" className="underline" onClick={() => removeFollowUp?.(item.id)}>
                <Text as="span" variant="meta">{item.state === "queued" ? "Remove from queue" : "Dismiss"}</Text>
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
