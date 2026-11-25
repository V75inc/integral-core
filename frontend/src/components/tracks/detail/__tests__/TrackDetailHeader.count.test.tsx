import type { ComponentProps } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { TrackDetailHeader } from '../TrackDetailHeader';
import { browserFilteredCount } from '../browserFilteredCount';

vi.mock('../../../sidebar/PinButton', () => ({ PinButton: () => null }));

afterEach(cleanup);

type Props = ComponentProps<typeof TrackDetailHeader>;
const base = {
  track: { id: 't1', title: 'Work', collaborators: [] },
  entriesTotal: 12, loadedEntries: 10, hasMoreEntries: true,
  onOpenCollaborators: vi.fn(),
} as unknown as Props;
const header = (extra: Partial<Props>) => <MemoryRouter><TrackDetailHeader {...base} {...extra} /></MemoryRouter>;

describe('TrackDetailHeader entry count', () => {
  it('says how many are shown while a browser filter is on, then the whole count again', () => {
    const { rerender } = render(header({ shownEntries: 5 }));
    expect(screen.getByText(/5 shown · 12 entries/)).toBeInTheDocument();
    rerender(header({ shownEntries: 2 }));
    expect(screen.getByText(/2 shown · 12 entries/)).toBeInTheDocument();
    rerender(header({ shownEntries: undefined }));
    expect(screen.getByText(/12 entries/)).not.toHaveTextContent('shown');
  });

  it('shows the whole Track total beside a filtered view', () => {
    render(header({ shownEntries: 1, entriesTotal: 4, trackTotal: 12 }));
    expect(screen.getByText(/1 shown · 12 entries/)).toBeInTheDocument();
  });
});

describe('browserFilteredCount', () => {
  it('counts while a type or tag is chosen and stops once both are cleared', () => {
    expect(browserFilteredCount('task', [], false, 5)).toBe(5);
    expect(browserFilteredCount('', ['urgent'], false, 2)).toBe(2);
    expect(browserFilteredCount('', ['urgent'], true, 2)).toBeUndefined();
    expect(browserFilteredCount('', [], false, 10)).toBeUndefined();
  });
});
