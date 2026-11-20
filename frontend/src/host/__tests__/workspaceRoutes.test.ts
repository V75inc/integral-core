import { describe, expect, it } from 'vitest';
import { getRegisteredWorkspaceRoutes, registerWorkspaceRoute } from '../registry';

describe('host workspace routes', () => {
  it('registers neutral host paths and returns a defensive copy', () => {
    registerWorkspaceRoute({ path: 'custom/assignment', element: null });
    const routes = getRegisteredWorkspaceRoutes();
    expect(routes.find(r => r.path === 'custom/assignment')).toBeDefined();
    routes.length = 0;
    expect(getRegisteredWorkspaceRoutes().length).toBeGreaterThan(0);
    registerWorkspaceRoute({ path: 'custom/assignment', element: 'replacement' });
    expect(getRegisteredWorkspaceRoutes().filter(r => r.path === 'custom/assignment')).toEqual([
      { path: 'custom/assignment', element: 'replacement' },
    ]);
  });
});

import { registerTrackNavigation, resolveHostTrackHref, resolveHostTrackRedirect } from '../registry';
import type { Track } from '../../types';
describe('host track navigation', () => {
  it('keeps Core defaults and accepts only local host routes', () => {
    const track = {id:'fixture',title:'Neutral'} as Track;
    expect(resolveHostTrackHref(track)).toBe('/tracks/fixture');
    expect(resolveHostTrackRedirect(track,new URLSearchParams())).toBeNull();
    registerTrackNavigation({id:'test',href:()=> '//outside.invalid',redirect:()=> 'https://outside.invalid'});
    expect(resolveHostTrackHref(track)).toBe('/tracks/fixture');
    expect(resolveHostTrackRedirect(track,new URLSearchParams())).toBeNull();
    registerTrackNavigation({id:'test',href:()=>'/\\outside.invalid',redirect:()=>'/\\outside.invalid'});
    expect(resolveHostTrackHref(track)).toBe('/tracks/fixture');
    expect(resolveHostTrackRedirect(track,new URLSearchParams())).toBeNull();
    registerTrackNavigation({id:'test',href:()=>'/custom/fixture',redirect:(_,q)=>'/custom/fixture?'+q.toString()});
    expect(resolveHostTrackHref(track)).toBe('/custom/fixture');
    expect(resolveHostTrackRedirect(track,new URLSearchParams('entry=123'))).toBe('/custom/fixture?entry=123');
  });
});
