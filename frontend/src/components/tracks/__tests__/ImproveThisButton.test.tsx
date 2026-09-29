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
      render(<ImproveThisButton target={target} />);

      fireEvent.click(screen.getByRole('button', { name: `Improve this ${target === 'entry' ? 'record' : target}` }));

      expect(listener).toHaveBeenCalledOnce();
      expect(consumePendingChatDraft()).toMatch(
        /open a draft model revision.*Do not publish.*I approve/i,
      );
      window.removeEventListener(OPEN_AI_CHAT_EVENT, listener);
    },
  );
});
