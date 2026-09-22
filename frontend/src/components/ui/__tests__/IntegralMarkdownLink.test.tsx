import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

import {
  IntegralMarkdownLink,
  isInternalAppHref,
} from '../IntegralMarkdownLink';

function renderLink(href: string, label = 'Open') {
  return renderToStaticMarkup(
    <MemoryRouter>
      <IntegralMarkdownLink href={href}>{label}</IntegralMarkdownLink>
    </MemoryRouter>,
  );
}

describe('isInternalAppHref', () => {
  it('accepts relative in-app paths', () => {
    expect(isInternalAppHref('/tracks/n.Track.abc')).toBe(true);
    expect(isInternalAppHref('/apps/n.App.xyz')).toBe(true);
  });

  it('accepts a canonical-host path normalized by markdown tooling', () => {
    expect(isInternalAppHref('https://integral.ai/tracks/n.Track.abc?entry=e-1')).toBe(true);
  });

  it('rejects external and unsafe hrefs', () => {
    expect(isInternalAppHref('https://example.com')).toBe(false);
    expect(isInternalAppHref('//evil.com')).toBe(false);
    expect(isInternalAppHref('javascript:alert(1)')).toBe(false);
  });
});

describe('IntegralMarkdownLink', () => {
  it('renders React Router link for internal paths', () => {
    const html = renderLink('/tracks/t-1', 'My track');
    expect(html).toContain('href="/tracks/t-1"');
    expect(html).toContain('My track');
    expect(html).not.toContain('target="_blank"');
  });

  it('renders external anchor for https URLs', () => {
    const html = renderLink('https://example.com/docs', 'Docs');
    expect(html).toContain('href="https://example.com/docs"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('keeps canonical-host record links in the current workspace', () => {
    const html = renderLink(
      'https://integral.ai/tracks/t-1?entry=e-1',
      'My record',
    );
    expect(html).toContain('href="/tracks/t-1?entry=e-1"');
    expect(html).not.toContain('target="_blank"');
  });
});
