import { useRef, useState, type FormEvent } from "react";
import apiClient from "../../../api/client";
import { Text } from "../../../ui";

type Receipt = { id: string; text: string };

export function SteeringComposer({
  workItemId,
  running,
  deliveredIds,
}: {
  workItemId: string;
  running: boolean;
  deliveredIds?: readonly string[];
}) {
  const [draft, setDraft] = useState("");
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(false);
  const pending = useRef<Receipt | null>(null);
  const inFlight = useRef(false);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!running || !text || inFlight.current) return;
    const correction = pending.current?.text === text
      ? pending.current
      : { id: crypto.randomUUID(), text };
    pending.current = correction;
    inFlight.current = true;
    setSending(true);
    setError(false);
    try {
      await apiClient.post(`/work-items/${encodeURIComponent(workItemId)}/steering`, {
        client_request_id: correction.id,
        text,
      });
      setReceipts((current) => [...current, correction]);
      setDraft("");
      pending.current = null;
    } catch {
      setError(true);
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {running && (
        <form onSubmit={send} className="flex items-end gap-2">
          <textarea
            aria-label="Correction for this turn"
            placeholder="Add a correction for this turn…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={sending}
            maxLength={4000}
            rows={2}
            className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--border-subtle)] bg-transparent px-3 py-2"
          />
          <button
            type="submit"
            disabled={sending || !draft.trim()}
            className="shrink-0 rounded-[var(--radius-input)] bg-[var(--cta-bg)] px-3 py-2 text-[var(--cta-fg)] disabled:opacity-50"
          >
            {sending ? "Sending…" : "Send correction"}
          </button>
        </form>
      )}
      {error && <div role="alert">Couldn’t confirm acceptance. Retry while the turn is running.</div>}
      <div role="status" className="flex flex-col gap-1 empty:hidden">
        {receipts.map(({ id, text }) => (
          <Text as="p" key={id} variant="meta" tone="subtle" className="break-words">
            {deliveredIds?.includes(id)
              ? "Delivered to this turn"
              : running ? "Accepted · Waiting for delivery" : "Accepted · Delivery unconfirmed"}
            {" — "}{text}
          </Text>
        ))}
      </div>
    </div>
  );
}
