import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { KebabMenu } from '../KebabMenu';

afterEach(cleanup);

it.each(['menu item', 'window'] as const)('dismisses Escape from %s without closing the enclosing dialog', target => {
  const parentEscape = vi.fn();
  window.addEventListener('keydown', parentEscape);
  try {
    render(<KebabMenu ariaLabel="More entry actions" items={[
      { key: 'post', label: 'Post to feed', onClick: vi.fn() },
    ]} />);
    const trigger = screen.getByRole('button', { name: 'More entry actions' });
    fireEvent.click(trigger);
    const action = screen.getByRole('menuitem', { name: 'Post to feed' });
    action.focus();
    fireEvent.keyDown(target === 'window' ? window : action, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(parentEscape).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
  } finally {
    window.removeEventListener('keydown', parentEscape);
  }
});
