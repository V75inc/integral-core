import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import JsonViewer from './JsonViewer';

afterEach(cleanup);

describe('bounded JSON identifiers', () => {
  it('uses visual ellipsis while retaining the complete identifier', () => {
    const id = 'a'.repeat(64);
    render(<JsonViewer data={{ work_item_id: id, body: 'Keep this complete review text.' }} />);
    const value = screen.getByTitle(id);
    expect(value).toHaveTextContent(id);
    expect(value).toHaveStyle({ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
    expect(screen.getByText(/Keep this complete review text/)).toHaveStyle({ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' });
  });
});
