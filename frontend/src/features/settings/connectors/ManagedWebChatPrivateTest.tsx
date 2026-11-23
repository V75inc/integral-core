import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';

import {
  isPrivateWebChatTestMessage,
  runManagedWebChatPrivateTest,
  type ManagedWebChatPrivateTestOwner,
} from '../../../api/managedWebChatPrivateTest';
import { Button } from '../../../components/ui/Button';
import { Surface, Text, Textarea } from '../../../ui';

export function ManagedWebChatPrivateTest({ owner, profileRevision }: {
  owner: Readonly<ManagedWebChatPrivateTestOwner>;
  profileRevision: number;
}) {
  const key = JSON.stringify([owner.principalId, owner.workspaceId, owner.sessionFence, owner.provisionId,
    owner.routeEpoch, owner.profileKey, owner.publishedVersion, profileRevision]);
  return <CurrentPrivateTest key={key} owner={owner} />;
}

function CurrentPrivateTest({ owner }: { owner: Readonly<ManagedWebChatPrivateTestOwner> }) {
  const id = useId();
  const pending = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [reply, setReply] = useState('');
  const [state, setState] = useState<'idle' | 'running' | 'unavailable'>('idle');

  const abort = useCallback(() => {
    const previous = pending.current;
    pending.current = null;
    previous?.abort();
  }, []);

  const close = useCallback(() => {
    abort();
    setText('');
    setReply('');
    setState('idle');
    setOpen(false);
  }, [abort]);

  useLayoutEffect(() => {
    function storage(event: StorageEvent) {
      if (!event.key || ['t75_user', 't75_token', 't75_refresh_token', 'integral.scope'].includes(event.key)) close();
    }
    function visibility() { if (document.hidden) close(); }
    const events = ['integral:tokens-updated', 'integral:logout', 'integral:records-changed', 'offline', 'pagehide'];
    events.forEach(event => window.addEventListener(event, close));
    window.addEventListener('storage', storage);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      abort();
      events.forEach(event => window.removeEventListener(event, close));
      window.removeEventListener('storage', storage);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [abort, close]);

  async function sendTest() {
    if (pending.current || !open || !isPrivateWebChatTestMessage(text)) return;
    const controller = new AbortController();
    pending.current = controller;
    setReply('');
    setState('running');
    try {
      const result = await runManagedWebChatPrivateTest(owner, text, controller.signal);
      if (pending.current === controller && !controller.signal.aborted) {
        setReply(result);
        setState('idle');
      }
    } catch {
      if (pending.current === controller && !controller.signal.aborted) setState('unavailable');
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  }

  return open ? (
    <Surface as="section" aria-label="Test Web Chat" border="subtle" radius="card" padding="md"
      className="flex min-w-0 flex-col gap-2">
      <Text as="h3" variant="body-sm" weight="semibold">Test Web Chat</Text>
      <Text id={`${id}-description`} as="p" variant="body-sm" tone="muted">
        Private test; does not publish or check public hosting. Uses the saved published profile and does not create a visitor conversation.
      </Text>
      <form className="flex min-w-0 flex-col items-start gap-2" onSubmit={event => { event.preventDefault(); void sendTest(); }}>
        <Text as="label" htmlFor={id} variant="body-sm" weight="medium">Test message</Text>
        <Textarea id={id} aria-describedby={`${id}-description`} value={text} maxLength={1000} rows={3}
          disabled={state === 'running'} onChange={event => setText(event.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="sm" disabled={state === 'running' || !isPrivateWebChatTestMessage(text)}>Send test</Button>
          <Button type="button" variant="ghost" size="sm" onClick={close}>Close test</Button>
        </div>
      </form>
      {state === 'running' && <Text as="p" variant="body-sm" role="status">Waiting for the private test reply…</Text>}
      {state === 'unavailable' && <Text as="p" variant="body-sm" role="status">Could not verify a private reply. Nothing was published.</Text>}
      {reply && <div role="status">
        <Text as="h4" variant="body-sm" weight="semibold">Test reply</Text>
        <Text as="p" variant="body-sm" className="whitespace-pre-wrap break-words">{reply}</Text>
      </div>}
    </Surface>
  ) : (
    <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(true)}>Test Web Chat</Button>
  );
}
