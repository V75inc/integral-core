import { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TaggableComposer } from './TaggableComposer';
vi.mock('./ResourceTagPopover', () => ({
  prefetchTagResources: vi.fn(),
  ResourceTagPopover: () => null,
}));
beforeEach(() =>
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  ),
);
afterEach(() => vi.unstubAllGlobals());
function Composer() {
  const [value, setValue] = useState('');
  return (
    <TaggableComposer
      value={value}
      onChange={setValue}
      entityRefs={[]}
      onEntityRefsChange={() => {}}
      aria-label="Draft"
    />
  );
}
it('keeps plain pasted multiline text native and preserves an edit in the middle', () => {
  render(<Composer />);
  const input = screen.getByRole('textbox', {
    name: 'Draft',
  }) as HTMLTextAreaElement;
  act(() => input.focus());
  fireEvent.change(input, {
    target: {
      value: 'First line\nSecond line\nThird line',
      selectionStart: 11,
      selectionEnd: 11,
    },
  });
  expect(input).toHaveClass('taggable-composer-native');
  expect(document.querySelector('.taggable-composer-mirror')).toHaveAttribute(
    'hidden',
  );
  fireEvent.select(input, { target: { selectionStart: 11, selectionEnd: 11 } });
  fireEvent.change(input, {
    target: {
      value: 'First line\nUpdated second line\nThird line',
      selectionStart: 19,
      selectionEnd: 19,
    },
  });
  expect(input.selectionStart).toBe(19);
  expect(input.value).toContain('Updated second line');
});
