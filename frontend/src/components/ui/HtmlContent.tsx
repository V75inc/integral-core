import { sanitizeHtml } from '../../utils/sanitizeHtml';

export interface HtmlContentProps {
  html: string;
  className?: string;
  /** Smaller type and tighter blocks (feed cards, meta rows). */
  compact?: boolean;
}

/**
 * Read-only HTML body (email previews, rich HTML custom fields).
 * Always sanitized before injection.
 */
export function HtmlContent({ html, className = '', compact = false }: HtmlContentProps) {
  const safe = sanitizeHtml(html || '');
  if (!safe.trim()) {
    return <span className="italic opacity-60">—</span>;
  }
  const size = compact ? 'text-[0.8125rem]' : 'text-sm';
  return (
    <div
      className={`integral-html-content ${size} leading-relaxed text-[var(--text)] break-words [&_a]:text-[var(--accent)] [&_a]:underline [&_p]:mb-2 [&_p:last-child]:mb-0 ${className}`.trim()}
      dangerouslySetInnerHTML={{ __html: safe }}
    />
  );
}
