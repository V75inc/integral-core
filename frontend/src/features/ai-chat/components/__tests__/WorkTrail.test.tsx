import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  message: {status: {type: 'running'}, metadata: {}, parts: []},
}));
vi.mock('@assistant-ui/react', async importOriginal => ({
  ...await importOriginal<typeof import('@assistant-ui/react')>(),
  useAuiState: (selector: (value: typeof state) => unknown) => selector(state),
}));
vi.mock('../../AIChatSurface', () => ({useChatActivity: () => ({activityText: 'Reading records'})}));
import { WorkTrail } from '../Thread';

describe('activity disclosure', () => {
  it('centers the working mark with its label and supports reduced motion', () => {
    state.message.status.type = 'running';
    const {container} = render(<WorkTrail><span>Tool receipt</span></WorkTrail>);
    const mark = container.querySelector('.animate-agent-working');
    expect(mark).toHaveClass('inline-flex', 'items-center', 'justify-center', 'motion-reduce:animate-none');
    expect(mark?.parentElement).toHaveClass('relative', '-top-px', 'h-4', 'w-4', 'items-center', 'justify-center', 'leading-none');
    expect(screen.getByRole('status')).toHaveClass('inline-flex', 'min-h-4', 'items-center', 'leading-4');
    expect(screen.getByText('Reading records')).toHaveClass('animate-status-reveal', 'motion-reduce:animate-none');
  });

  it('starts collapsed and preserves a manual expansion through completion', () => {
    const {rerender} = render(<WorkTrail><span>Tool receipt</span></WorkTrail>);
    const trigger = screen.getByRole('button');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(screen.getByText('Tool receipt')).toBeVisible();
    state.message.status.type = 'complete';
    rerender(<WorkTrail><span>Tool receipt</span></WorkTrail>);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Tool receipt')).toBeVisible();
    expect(screen.getByText(/Private model reasoning is not displayed/)).toBeVisible();
  });
});
