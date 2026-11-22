import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Button } from '../../components/ui/Button';
import { Surface, Text, Textarea } from '../../ui';

export interface VisitorMessage {
  id: string;
  role: 'visitor' | 'assistant';
  text: string;
}

type SessionReply =
  | { kind: 'ready'; messages: readonly VisitorMessage[] }
  | { kind: 'unavailable' }
  | { kind: 'expired' };

type SendReply = SessionReply | { kind: 'pending' };
type StartMode = 'resume' | 'new';

/**
 * The host-supplied transport owns routing, visitor credentials and server-side
 * idempotency. The component never receives or chooses workspace, profile or
 * conversation IDs. A retry submits the exact original ID and text. Keep one
 * transport instance per mounted visitor session and replace it on route change.
 */
export interface PublicWebChatTransport {
  start(input: { mode: StartMode; signal: AbortSignal }): Promise<SessionReply>;
  send(input: { id: string; text: string; signal: AbortSignal }): Promise<SendReply>;
}

type Attempt = { id: string; text: string };
type ChatState =
  | { phase: 'loading' | 'unavailable' | 'expired' }
  | { phase: 'load-error'; mode: StartMode }
  | { phase: 'ready'; messages: readonly VisitorMessage[]; draft: string; attempt?: Attempt;
      sendStatus?: 'sending' | 'error' | 'pending' };

const INITIAL: ChatState = { phase: 'loading' };
const MESSAGE_LIMIT = 1000;

function fromSession(reply: SessionReply): ChatState {
  if (reply.kind === 'ready') return { phase: 'ready', messages: reply.messages, draft: '' };
  return { phase: reply.kind };
}

/** Unmounted text-only visitor surface. Requires an explicitly wired host. */
export function PublicWebChat({ title, transport }: {
  title: string;
  transport: PublicWebChatTransport;
}) {
  const [view, setView] = useState<{ transport: PublicWebChatTransport; chat: ChatState }>({ transport, chat: INITIAL });
  const operation = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const messageId = useId();
  const chat = view.transport === transport ? view.chat : INITIAL;
  const update = useCallback((next: ChatState) => setView({ transport, chat: next }), [transport]);

  const start = useCallback(async (mode: StartMode) => {
    if (operation.current) return;
    const owner = new AbortController();
    operation.current = owner;
    try {
      const result = await transport.start({ mode, signal: owner.signal });
      if (!owner.signal.aborted) update(fromSession(result));
    } catch {
      if (!owner.signal.aborted) update({ phase: 'load-error', mode });
    } finally {
      if (operation.current === owner) operation.current = null;
    }
  }, [transport, update]);

  useEffect(() => {
    // Let an immediate unmount/replacement (including Strict Mode) cancel
    // setup before opening a session.
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) void start('resume'); });
    return () => {
      cancelled = true;
      operation.current?.abort();
      operation.current = null;
    };
  }, [start]);

  function retryStart(mode: StartMode) {
    if (operation.current) return;
    update(INITIAL);
    void start(mode);
  }

  async function send() {
    if (chat.phase !== 'ready' || operation.current) return;
    const text = chat.draft.trim();
    if (!chat.attempt && (!text || text.length > MESSAGE_LIMIT)) return;
    const attempt = chat.attempt ?? { id: globalThis.crypto.randomUUID(), text };
    const owner = new AbortController();
    operation.current = owner;
    update({ ...chat, attempt, sendStatus: 'sending' });
    try {
      const result = await transport.send({ ...attempt, signal: owner.signal });
      if (owner.signal.aborted) return;
      update(result.kind === 'pending'
        ? { ...chat, attempt, sendStatus: 'pending' }
        : fromSession(result));
    } catch {
      if (!owner.signal.aborted) update({ ...chat, attempt, sendStatus: 'error' });
    } finally {
      if (operation.current === owner) operation.current = null;
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 py-6 sm:px-6">
      <Text as="h1" variant="heading-md" weight="semibold" className="break-words">{title}</Text>
      {chat.phase !== 'ready' ? (
        <Surface border="subtle" radius="card" padding="md" className="flex flex-col items-start gap-3">
          {chat.phase === 'loading' && <Text as="p" role="status">Opening chat…</Text>}
          {chat.phase === 'unavailable' && <>
            <Text as="p" role="status">This chat is unavailable.</Text>
            <Button variant="secondary" size="sm" onClick={() => retryStart('resume')}>Check again</Button>
          </>}
          {chat.phase === 'expired' && <>
            <Text as="p" role="status">This chat has expired. Start a new one to continue.</Text>
            <Button size="sm" onClick={() => retryStart('new')}>Start a new chat</Button>
          </>}
          {chat.phase === 'load-error' && <>
            <Text as="p" role="alert">Could not open chat. Try again.</Text>
            <Button variant="secondary" size="sm" onClick={() => retryStart(chat.mode)}>Retry opening chat</Button>
          </>}
        </Surface>
      ) : (
        <>
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            {chat.messages.length === 0 && <Text as="p" tone="muted">Send a message to begin.</Text>}
            <div role="log" aria-label="Conversation" aria-relevant="additions text" className="min-w-0">
              <ol className="flex min-w-0 flex-col gap-3">
                {chat.messages.map(message => (
                  <Surface as="li" key={message.id} tone={message.role === 'visitor' ? 'panel-2' : 'panel'}
                    border="subtle" radius="card" padding="md"
                    className={message.role === 'visitor' ? 'max-w-full self-end' : 'max-w-full self-start'}>
                    <Text as="p" variant="meta" tone="muted">{message.role === 'visitor' ? 'You' : 'Assistant'}</Text>
                    <Text as="p" className="whitespace-pre-wrap [overflow-wrap:anywhere]">{message.text}</Text>
                  </Surface>
                ))}
              </ol>
            </div>
          </div>
          <Surface border="subtle" radius="card" padding="md">
            <form ref={form} className="flex min-w-0 flex-col gap-3" onSubmit={event => { event.preventDefault(); void send(); }}>
              <Text as="label" htmlFor={messageId} variant="body-sm" weight="medium">Message</Text>
              <Textarea
                id={messageId}
                value={chat.draft}
                maxLength={MESSAGE_LIMIT}
                readOnly={Boolean(chat.attempt)}
                onChange={event => { if (!chat.attempt) update({ ...chat, draft: event.target.value }); }}
                onKeyDown={event => {
                  if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
                  event.preventDefault();
                  form.current?.requestSubmit();
                }}
              />
              {chat.sendStatus === 'sending' && <Text as="p" variant="body-sm" role="status">Waiting for a reply…</Text>}
              {chat.sendStatus === 'pending' && <Text as="p" variant="body-sm" role="status">Your message is still being processed.</Text>}
              {chat.sendStatus === 'error' && <Text as="p" variant="body-sm" role="alert">Could not confirm delivery. Retry the same message.</Text>}
              <div className="flex justify-end">
                <Button type="submit" disabled={chat.sendStatus === 'sending' || !chat.draft.trim()} size="sm" className="min-h-11">
                  {chat.sendStatus === 'sending' ? 'Sending…'
                    : chat.sendStatus === 'pending' ? 'Check for reply'
                    : chat.sendStatus === 'error' ? 'Retry message' : 'Send message'}
                </Button>
              </div>
            </form>
          </Surface>
        </>
      )}
    </main>
  );
}
