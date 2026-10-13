import { Text } from '../../ui';
import { useState } from 'react';
import apiClient from '../../api/client';
import { Button } from '../ui';

type ResourceType = 'app' | 'track' | 'entry';
interface Source { source_id: string; user_id: string; resource_id: string; source: string; role?: string; state?: string; link_id?: string }
interface Snapshot {
  effective_role: string | null; links_visible: boolean; complete?: boolean;
  governance?: { source: string }; effective_access?: Array<{ user_id: string; role: string | null }>;
  sources?: Source[];
}

export function ResourceAccessReview({ resourceType, resourceId }: { resourceType: ResourceType; resourceId: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    const result = await apiClient.get<Snapshot>(`/${resourceType}s/${resourceId}/access`);
    if (result.data.links_visible && result.data.complete !== true) throw new Error('Incomplete access review');
    setSnapshot(result.data);
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); }
    catch { setSnapshot(null); setError('Access review could not be confirmed. Reload before retrying.'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Effective access review" className="space-y-3">
    <Button variant="secondary" disabled={busy} onClick={() => run(load)}>Review effective access</Button>
    {error && <p role="alert" className="text-sm text-[var(--danger-fg)]">{error}</p>}
    {snapshot && <>
      <p>Your current role: {snapshot.effective_role || 'none'}{snapshot.governance ? ' · workspace owner governance' : ''}.</p>
      {snapshot.links_visible ? <>
        <Text as="p" variant="body" tone="muted" >Current roles include all eligible sources. An inherited exclusion removes inherited access; a subtree block removes direct and inherited access. Disabling a link prevents new redemptions; revoke its accepted source below to remove that grant.</Text>
        <ul className="space-y-3">{snapshot.effective_access?.map(person => {
          const sources = snapshot.sources?.filter(source => source.user_id === person.user_id) || [];
          const blocked = sources.some(source => source.resource_id === resourceId && source.source === 'ResourceAccessBlock' && source.state === 'blocked');
          return <li key={person.user_id} className="app-card p-3 space-y-2">
            <p>{person.user_id} · current role: {person.role || 'none'}</p>
            <ul>{sources.map(source => <li key={source.source_id}>{source.source} · {source.role || source.state || 'retained'} · {source.resource_id === resourceId ? 'This resource' : 'Inherited resource'}
              {source.link_id && source.state === 'active' && <Button variant="secondary" disabled={busy} onClick={() => run(async () => {
                await apiClient.delete(`/shares/${source.link_id}/grants/${person.user_id}`); await load();
              })}>Revoke this accepted link grant</Button>}
            </li>)}</ul>
            <Button variant="secondary" disabled={busy} onClick={() => run(async () => {
              await apiClient.put(`/access/${resourceType}/${resourceId}/blocks`, { subject_id: person.user_id, blocked: !blocked, reason: 'Owner access review' }); await load();
            })}>{blocked ? 'Release this subtree block' : 'Block access to this subtree'}</Button>
          </li>;
        })}</ul>
      </> : <Text as="p" variant="body" tone="muted" >A resource manager or workspace owner can review other people’s access.</Text>}
    </>}
  </section>;
}
