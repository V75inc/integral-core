/**
 * Two small Thread pieces with a large blast radius.
 *
 *  - ActivityStrip returned null whenever no turn was running, BEFORE looking
 *    at `streamError`. Every error worth showing is one that ended the turn,
 *    so the error branch was unreachable and failures were silent.
 *  - SourceView rendered `href={part.url}` straight from a tool result,
 *    bypassing the scheme allow-list every markdown link goes through.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const activity = {
  activityText: null as string | null,
  isRunning: false,
  streamError: null as string | null,
};

vi.mock('../../AIChatSurface', () => ({
  useChatActivity: () => activity,
}));

import { ActivityStrip, SourceView } from '../Thread';
import { hasAssistantDebugPayload } from '../assistantMessagePresentation';

afterEach(() => {
  cleanup();
  activity.activityText = null;
  activity.isRunning = false;
  activity.streamError = null;
});

describe('ActivityStrip', () => {
  it('shows a stream error even when no turn is running', () => {
    activity.streamError = 'This conversation is already responding.';
    render(<ActivityStrip />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'This conversation is already responding.',
    );
  });

  it('still renders nothing when idle and error-free', () => {
    const { container } = render(<ActivityStrip />);
    expect(container).toBeEmptyDOMElement();
  });

  it('prefers the error over the activity text while running', () => {
    activity.isRunning = true;
    activity.activityText = 'Filing your content';
    activity.streamError = 'Something went wrong';
    render(<ActivityStrip />);
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
    expect(screen.queryByText(/Filing your content/)).toBeNull();
  });
});

describe('SourceView', () => {
  it('links a safe http(s) source', () => {
    render(
      <MemoryRouter>
        <SourceView part={{ url: 'https://example.com/doc', title: 'Doc' }} />
      </MemoryRouter>,
    );
    const a = screen.getByRole('link', { name: /Doc/ });
    expect(a).toHaveAttribute('href', 'https://example.com/doc');
    expect(a).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it.each(['javascript:alert(1)', 'data:text/html,<b>x</b>', 'vbscript:x'])(
    'renders %s as inert text, not an anchor',
    (url) => {
      render(<SourceView part={{ url, title: 'Bad' }} />);
      expect(screen.queryByRole('link')).toBeNull();
      expect(screen.getByTestId('chat-source-unsafe')).toHaveTextContent('Bad');
    },
  );
});

describe('metadata-only assistant messages', () => {
  it('exposes actions only when a real debug payload exists', () => {
    expect(
      hasAssistantDebugPayload({
        finalPayload: {
          run_id: 'run-1',
          claim_provenance: { tools: [] },
        },
      }),
    ).toBe(true);
    expect(hasAssistantDebugPayload({ finalContent: '' })).toBe(false);
    expect(hasAssistantDebugPayload(undefined)).toBe(false);
  });
});
