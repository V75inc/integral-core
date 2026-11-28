import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AssistantRuntimeProvider, useAuiState, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react';
import { MessageCircle } from 'lucide-react';

import { Thread } from '../../vendor/jvmessenger/app/chat/Thread';
import { MarkdownText } from '../../vendor/jvmessenger/components/assistant-ui/markdown-text';
import { CONFIG_DEFAULTS } from '../../vendor/jvmessenger/shared/config';
import './visitorChat.css';
import { useVisitorDarkTheme } from './visitorTheme';

import { Button } from '../../components/ui/Button';
import { Surface, Text } from '../../ui';

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
  | { phase: 'ready'; messages: readonly VisitorMessage[]; attempt?: Attempt;
      sendStatus?: 'sending' | 'error' | 'pending'; freshReplyId?: string };

const INITIAL: ChatState = { phase: 'loading' };
const MESSAGE_LIMIT = 1000;

function fromSession(reply: SessionReply): ChatState {
  if (reply.kind === 'ready') return { phase: 'ready', messages: reply.messages };
  return { phase: reply.kind };
}

/** Unmounted text-only visitor surface. Requires an explicitly wired host. */
export function PublicWebChat({ title, transport, restartMode = 'transport' }: {
  title: string;
  transport: PublicWebChatTransport;
  restartMode?: 'transport' | 'reopen-embed';
}) {
  const dark = useVisitorDarkTheme();
  useLayoutEffect(() => {
    const previous = document.documentElement.dataset.theme;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    return () => {
      if (previous === undefined) delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = previous;
    };
  }, [dark]);
  const [view, setView] = useState<{ transport: PublicWebChatTransport; chat: ChatState }>({ transport, chat: INITIAL });
  const operation = useRef<AbortController | null>(null);
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

  async function send(input?: string) {
    if (chat.phase !== 'ready' || operation.current) return;
    const text = input?.trim() ?? '';
    if (!chat.attempt && (!text || text.length > MESSAGE_LIMIT)) return;
    const attempt = chat.attempt ?? { id: globalThis.crypto.randomUUID(), text };
    const owner = new AbortController();
    operation.current = owner;
    update({ ...chat, attempt, sendStatus: 'sending' });
    try {
      const result = await transport.send({ ...attempt, signal: owner.signal });
      if (owner.signal.aborted) return;
      const next: ChatState = result.kind === 'pending'
        ? { ...chat, attempt, sendStatus: 'pending' }
        : fromSession(result);
      if (next.phase === 'ready' && result.kind === 'ready') {
        const reply = next.messages[next.messages.length - 1];
        if (reply?.role === 'assistant' && !chat.messages.some(message => message.id === reply.id)) {
          next.freshReplyId = reply.id;
        }
      }
      update(next);
    } catch {
      if (!owner.signal.aborted) update({ ...chat, attempt, sendStatus: 'error' });
    } finally {
      if (operation.current === owner) operation.current = null;
    }
  }

  return (
    <main className={`visitor-chat ${dark ? 'theme-dark' : ''} ${restartMode === 'reopen-embed' ? 'visitor-chat-popup' : 'visitor-chat-page'}`}>
      <header className="visitor-header"><div className="visitor-header-inner">
        <span className="visitor-mark" aria-hidden="true"><MessageCircle size={20} /></span>
        <Text as="h1" variant="heading-sm" weight="semibold">{title}</Text>
      </div></header>
      {chat.phase !== 'ready' ? (
        <Surface border="subtle" radius="card" padding="md" className="visitor-state flex flex-col items-start gap-3">
          {chat.phase === 'loading' && <Text as="p" role="status">Opening chat…</Text>}
          {chat.phase === 'unavailable' && <>
            <Text as="p" role="status">This chat is unavailable.</Text>
            {restartMode === 'transport' &&
              <Button variant="secondary" size="sm" onClick={() => retryStart('resume')}>Check again</Button>}
          </>}
          {chat.phase === 'expired' && <>
            <Text as="p" role="status">{restartMode === 'transport'
              ? 'This chat has expired. Start a new one to continue.'
              : 'This chat has expired. Reopen this chat from the website to start again.'}</Text>
            {restartMode === 'transport' &&
              <Button size="sm" onClick={() => retryStart('new')}>Start a new chat</Button>}
          </>}
          {chat.phase === 'load-error' && <>
            <Text as="p" role="alert">{restartMode === 'transport'
              ? 'Could not open chat. Try again.'
              : 'Could not open chat. Reopen this chat from the website to try again.'}</Text>
            {restartMode === 'transport' &&
              <Button variant="secondary" size="sm" onClick={() => retryStart(chat.mode)}>Retry opening chat</Button>}
          </>}
        </Surface>
      ) : (
        <ReadyVisitorChat chat={chat} onSend={send} title={title} />
      )}
    </main>
  );
}

type ReadyChatState = Extract<ChatState, { phase: 'ready' }>;

function convertVisitorMessage(message: VisitorMessage): ThreadMessageLike {
  return {
    id: message.id,
    role: message.role === 'visitor' ? 'user' : 'assistant',
    content: [{ type: 'text', text: message.text }],
  };
}

function safeVisitorLink(value: string, key: string): string | undefined {
  if (key !== 'href') return undefined;
  try {
    const url = new URL(value);
    if (url.protocol === 'mailto:') return value;
    if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) return value;
  } catch {
    // Relative and malformed links have no trusted base in the visitor view.
  }
  return undefined;
}

const visitorMarkdownComponents = {
  img: ({ alt }: { alt?: string }) => alt ? <span>{alt}</span> : null,
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => href
    ? <a className="text-primary underline" href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
};

function ProgressiveReply({ text }: { text: string }) {
  const [visible, setVisible] = useState(0);
  useEffect(() => {
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let start: number | undefined;
    const duration = Math.min(650, Math.max(160, text.length * 12));
    function finish() {
      cancelAnimationFrame(frame);
      setVisible(text.length);
    }
    function reveal(now: number) {
      start ??= now;
      const count = Math.min(text.length, Math.ceil((now - start) / duration * text.length));
      setVisible(count);
      if (count < text.length) frame = requestAnimationFrame(reveal);
    }
    if (motion?.matches) finish();
    else frame = requestAnimationFrame(reveal);
    motion?.addEventListener('change', finish);
    return () => {
      cancelAnimationFrame(frame);
      motion?.removeEventListener('change', finish);
    };
  }, [text]);
  const preprocess = useCallback(() => text.slice(0, visible), [text, visible]);
  return <div aria-hidden={visible < text.length || undefined}>
    <MarkdownText preprocess={preprocess} skipHtml smooth={false} components={visitorMarkdownComponents} urlTransform={safeVisitorLink} />
  </div>;
}

const FreshReply = createContext<string | undefined>(undefined);

function VisitorAssistantText({ text }: { text: string }) {
  const fresh = useContext(FreshReply);
  const id = useAuiState(state => state.message.id);
  return id === fresh ? <ProgressiveReply text={text} /> : <MarkdownText skipHtml smooth={false} components={visitorMarkdownComponents} urlTransform={safeVisitorLink} />;
}

const unused = () => {};
const noSession = async () => null;
const noToken = () => undefined;

function ReadyVisitorChat({ chat, onSend, title }: {
  chat: ReadyChatState;
  onSend: (input?: string) => Promise<void>;
  title: string;
}) {
  const running = chat.sendStatus === 'sending';
  const active = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const sendText = useCallback((text: string) => {
    const trimmed = text.trim();
    if (chat.attempt || !trimmed || trimmed.length > MESSAGE_LIMIT) return;
    queueMicrotask(() => { if (active.current) void onSend(trimmed); });
  }, [chat.attempt, onSend]);
  const messages = useMemo(() => {
    const result = chat.messages.map(convertVisitorMessage);
    if (chat.attempt && !chat.messages.some(message => message.id === chat.attempt?.id)) {
      result.push(convertVisitorMessage({ ...chat.attempt, role: 'visitor' }));
    }
    return result;
  }, [chat.messages, chat.attempt]);
  const runtime = useExternalStoreRuntime({ messages, convertMessage: message => message, isRunning: running,
    onNew: async message => {
      const [part] = message.content;
      if (message.role === 'user' && message.content.length === 1 && part?.type === 'text') sendText(part.text);
    },
  });
  const turnError = chat.sendStatus === 'error' ? 'Could not confirm delivery. Retry the same message.'
    : chat.sendStatus === 'pending' ? 'Your message is still being processed.' : null;
  return (
    <div className="visitor-thread visitor-upstream-thread">
      <AssistantRuntimeProvider runtime={runtime}>
        <FreshReply.Provider value={chat.freshReplyId}>
          <Thread
            config={{ ...CONFIG_DEFAULTS, title, greeting: 'Send a message to begin.', agentId: 'integral-visitor', agentUrl: '', sound: false }}
            assistantText={VisitorAssistantText}
            visitorInput={{ label: 'Message', maxLength: MESSAGE_LIMIT, locked: Boolean(chat.attempt) }}
            visitorLog
            sendText={sendText}
            getToken={noToken}
            ensureSession={noSession}
            hasSession
            attachments={[]}
            addAttachment={unused}
            removeAttachment={unused}
            suggestions={[]}
            activity={running ? [{ id: 'delivery', label: 'Waiting for a reply', status: 'running' }] : []}
            turnError={turnError}
            retryLabel={chat.sendStatus === 'pending' ? 'Check for reply' : 'Retry message'}
            retry={() => { if (chat.attempt && !running) void onSend(); }}
          />
          {running && <span className="sr-only" role="status">Waiting for a reply…</span>}
        </FreshReply.Provider>
      </AssistantRuntimeProvider>
    </div>
  );
}
