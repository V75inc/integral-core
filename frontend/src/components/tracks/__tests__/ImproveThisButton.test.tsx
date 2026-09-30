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
      render(<ImproveThisButton target={target} subjectName="Customer Reviews" />);

      fireEvent.click(screen.getByRole('button', { name: `Improve this ${target === 'entry' ? 'record' : target}` }));

      expect(listener).toHaveBeenCalledOnce();
      expect(consumePendingChatDraft()).toMatch(
        /currently open .*“Customer Reviews” .*existing schema.*open a draft model revision.*Do not publish.*I approve/i,
      );
      window.removeEventListener(OPEN_AI_CHAT_EVENT, listener);
    },
  );

  it('keeps an untitled target usable without inserting an empty label', () => {
    render(<ImproveThisButton target="track" subjectName="  " />);

    fireEvent.click(screen.getByRole('button', { name: 'Improve this track' }));

    expect(consumePendingChatDraft()).toMatch(/currently open track using its existing schema/i);
  });
});
