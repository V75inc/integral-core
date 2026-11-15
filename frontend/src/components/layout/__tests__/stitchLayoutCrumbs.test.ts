import { describe, expect, it } from 'vitest';
import { stitchLayoutCrumbs } from '../stitchLayoutCrumbs';

describe('stitchLayoutCrumbs', () => {
  it('keeps a page-published workspace crumb on /workspaces/:id/members', () => {
    const trail = stitchLayoutCrumbs(
      [
        { label: 'Acme Org', to: '/workspaces/ws-1' },
        { label: 'Members' },
      ],
      '/workspaces/ws-1/members',
      { id: 'ws-1', name: 'Acme Org' },
    );
    expect(trail.map(c => c.label)).toEqual(['Home', 'Workspaces', 'Acme Org', 'Members']);
    expect(trail[1].to).toBe('/workspaces');
    expect(trail[2].to).toBe('/workspaces/ws-1');
  });

  it('links to Workspaces from a workspace detail but does not duplicate the list page', () => {
    expect(stitchLayoutCrumbs([{ label: 'Acme' }], '/workspaces/ws-1')).toEqual([
      { label: 'Home', to: '/' }, { label: 'Workspaces', to: '/workspaces' }, { label: 'Acme' },
    ]);
    expect(stitchLayoutCrumbs([{ label: 'Workspaces' }], '/workspaces')).toEqual([
      { label: 'Home', to: '/' }, { label: 'Workspaces' },
    ]);
  });

  it('dedupes workspace name only when Layout injects a workspace prefix', () => {
    const trail = stitchLayoutCrumbs(
      [
        { label: 'Acme Org', to: '/workspaces/ws-1' },
        { label: 'Apps' },
      ],
      '/apps',
      { id: 'ws-1', name: 'Acme Org' },
    );
    expect(trail.map(c => c.label)).toEqual(['Home', 'Acme Org', 'Apps']);
  });

  it('names no workspace while the page is still loading its record', () => {
    // The active workspace is still the previous page's until the record loads.
    const trail = stitchLayoutCrumbs(
      [{ label: 'Tracks', to: '/tracks' }, { label: 'Loading…' }],
      '/tracks/t-1',
      { id: 'ws-old', name: 'Previous Workspace' },
    );
    expect(trail.map(c => c.label)).toEqual(['Home', 'Tracks', 'Loading…']);
  });
});
