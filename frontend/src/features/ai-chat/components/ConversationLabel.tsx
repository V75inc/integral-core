import { useEffect, useId, useRef, useState } from 'react';
import { Text, Surface } from '../../../ui';
import { createPortal } from 'react-dom';

/** Compact single-line title; the full name remains available outside the scrolling rail. */
export function ConversationLabel({ title, lastActivity, createdAt, messageCount, working = false }: {
  title: string;
  lastActivity?: string | null;
  createdAt?: string;
  messageCount?: number;
  working?: boolean;
}) {
  const tooltipId = useId();
  const labelRef = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const reveal = (element: HTMLElement) => {
    window.dispatchEvent(new CustomEvent('integral:conversation-title-reveal', { detail: tooltipId }));
    const rect = element.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(rect.right + 8, window.innerWidth - Math.min(380, window.innerWidth - 16) - 8)),
      top: Math.max(8, Math.min(rect.top, window.innerHeight - 230)),
    });
  };
  useEffect(() => {
    const label = labelRef.current;
    const trigger = label?.closest('button');
    if (!label || !trigger) return;
    const focus = () => reveal(label);
    const blur = () => setPosition(null);
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') blur(); };
    const otherPreview = (e: Event) => { if ((e as CustomEvent<string>).detail !== tooltipId) blur(); };
    window.addEventListener('integral:conversation-title-reveal', otherPreview);
    window.addEventListener('scroll', blur, true);
    window.addEventListener('keydown', key);
    trigger.addEventListener('focus', focus);
    trigger.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('integral:conversation-title-reveal', otherPreview);
      window.removeEventListener('scroll', blur, true);
      window.removeEventListener('keydown', key);
      trigger.removeEventListener('focus', focus);
      trigger.removeEventListener('blur', blur);
    };
  }, [tooltipId]);
  return (
    <span ref={labelRef} className="min-w-0 flex-1" title={title}
      onMouseEnter={e => reveal(e.currentTarget)} onMouseLeave={() => setPosition(null)}
      onFocusCapture={e => reveal(e.currentTarget)} onBlurCapture={() => setPosition(null)}>
      <span className="block truncate text-[13px] leading-[17px]" aria-describedby={position ? tooltipId : undefined}>{title}</span>
      {lastActivity && <Text as="span" variant="meta" tone="subtle" className="mt-0.5 block pr-7 tabular-nums"><time dateTime={lastActivity} title={new Date(lastActivity).toLocaleString()}>
        {new Date(lastActivity).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </time></Text>}
      {position && createPortal(<Surface as="span" id={tooltipId} role="tooltip" tone="panel" border="default" radius="input" padding="sm" elevation="pop"
        style={position} className="pointer-events-none fixed z-popover w-[min(380px,calc(100vw-16px))] [overflow-wrap:anywhere]"><Text as="span" variant="body-sm" weight="medium" className="block">{title}</Text>
        <span className="mt-2 flex flex-col gap-1">
          {lastActivity && <Text as="span" variant="meta" tone="muted">Last activity: {new Date(lastActivity).toLocaleString()}</Text>}
          {createdAt && <Text as="span" variant="meta" tone="muted">Created: {new Date(createdAt).toLocaleString()}</Text>}
          {typeof messageCount === 'number' && Number.isFinite(messageCount) && messageCount >= 0 && <Text as="span" variant="meta" tone="muted">{messageCount} {messageCount === 1 ? 'message' : 'messages'}</Text>}
          {working && <Text as="span" variant="meta" tone="info">Working…</Text>}
        </span>
      </Surface>, document.body)}
    </span>
  );
}
