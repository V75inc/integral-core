import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ImproveThisButton, type ImprovementTarget } from '../ImproveThisButton';
import {
  consumePendingChatDraft,
  OPEN_AI_CHAT_EVENT,
} from '../../../features/ai-chat/chatHandoff';

afterEach(() => {
  sessionStorage.clear();
});

describe('ImproveThisButton', () => {
  it.each<ImprovementTarget>(['entry', 'track', 'view'])(
    'opens chat with a review-only draft for a focused %s',
    target => {
      const listener = vi.fn();
      window.addEventListener(OPEN_AI_CHAT_EVENT, listener);
      render(
        <ImproveThisButton
          target={target}
          subjectName="Customer Reviews"
          subjectId="n.Resource.focused"
          trackId="n.Track.customer-reviews"
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: `Improve this ${target === 'entry' ? 'record' : target}` }));

      expect(listener).toHaveBeenCalledOnce();
      const draft = consumePendingChatDraft() ?? '';
      expect(draft).toMatch(
        /currently open .*“Customer Reviews” .*Focused .* ID: "n\.Resource\.focused".*existing schema.*integral_model skill.*Do not publish.*I approve/i,
      );
      if (target === 'track') {
        expect(draft).toContain(
          'integral_describe_model for the focused track with track_id="n.Track.customer-reviews"',
        );
        expect(draft).toContain('integral_recommend_customizations');
        expect(draft).toContain('Do not call integral_draft_new_model');
      } else {
        expect(draft).toContain('Parent track ID: "n.Track.customer-reviews"');
      }
      window.removeEventListener(OPEN_AI_CHAT_EVENT, listener);
    },
  );

  it('keeps an untitled target usable without inserting an empty label', () => {
    render(
      <ImproveThisButton
        target="track"
        subjectName="  "
        subjectId="n.Track.untitled"
        trackId="n.Track.untitled"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Improve this track' }));

    expect(consumePendingChatDraft()).toMatch(
      /currently open track Focused track ID: "n\.Track\.untitled".*existing schema.*integral_recommend_customizations/i,
    );
  });
});
