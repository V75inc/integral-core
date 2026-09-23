import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { isSafeHref, sanitizeMarkdownHref } from '../../utils/safeHref';

export function isInternalAppHref(href: string | undefined): boolean {
  return internalAppHref(href) !== undefined;
}

/**
 * Return a local router target for Core paths. Markdown tooling normalizes a
 * relative link against ``https://integral.ai`` before this component sees it;
 * retaining that host would send a self-hosted user away from their active
 * workspace. Treat the canonical public host as another spelling of an
 * in-app path, while leaving every other external host external.
 */
function internalAppHref(href: string | undefined): string | undefined {
  if (!isSafeHref(href)) return undefined;
  const trimmed = href!.trim();
  if (trimmed.startsWith('/') && !trimmed.startsWith('//')) return trimmed;

  try {
    const url = new URL(trimmed);
    if (url.protocol === 'https:' && url.hostname === 'integral.ai') {
      return `${url.pathname}${url.search}${url.hash}`;
    }
  } catch {
    // isSafeHref already rejected malformed URLs. Keep this defensive guard
    // so a future validator change cannot turn a malformed external link into
    // an in-app navigation target.
  }
  return undefined;
}

const linkClassName =
  'text-[var(--link)] hover:text-[var(--link-hover)] underline underline-offset-2 break-all';

export interface IntegralMarkdownLinkProps
  extends Omit<ComponentPropsWithoutRef<'a'>, 'href'> {
  href?: string;
  children?: ReactNode;
}

/**
 * Markdown anchor that uses React Router for in-app paths and opens external
 * URLs in a new tab.
 */
export function IntegralMarkdownLink({
  href,
  children,
  className,
  ...props
}: IntegralMarkdownLinkProps) {
  const safeHref = sanitizeMarkdownHref(href);
  if (!safeHref) {
    return <span className={className} {...props}>{children}</span>;
  }

  const internalHref = internalAppHref(safeHref);
  if (internalHref) {
    return (
      <Link to={internalHref} className={className ?? linkClassName} {...props}>
        {children}
      </Link>
    );
  }

  return (
    <a
      href={safeHref}
      target="_blank"
      rel="noopener noreferrer"
      className={className ?? linkClassName}
      {...props}
    >
      {children}
    </a>
  );
}
