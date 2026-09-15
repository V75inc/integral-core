import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { isSafeHref, sanitizeMarkdownHref } from '../../utils/safeHref';

export function isInternalAppHref(href: string | undefined): boolean {
  if (!isSafeHref(href)) return false;
  const trimmed = href!.trim();
  return trimmed.startsWith('/') && !trimmed.startsWith('//');
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

  if (isInternalAppHref(safeHref)) {
    return (
      <Link to={safeHref} className={className ?? linkClassName} {...props}>
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
