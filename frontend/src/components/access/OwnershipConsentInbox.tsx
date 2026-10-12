import { Text } from '../../ui';
import { useState } from 'react';
import { ownershipConsentApi, type OwnershipProposal } from '../../api/accessControl';
import { Button } from '../ui';

export function OwnershipConsentInbox({ onAccepted }: { onAccepted?: () => Promise<void> } = {}) {
  const [proposals, setProposals] = useState<OwnershipProposal[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function load() {
    setBusy(true); setMessage('');
    try {
      const result = await ownershipConsentApi.inbox();
      if (!result.complete) throw new Error('Incomplete inbox');
      setProposals(result.proposals);
    } catch { setProposals(null); setMessage('Ownership proposals could not be loaded.'); }
    finally { setBusy(false); }
  }
  async function respond(proposal: OwnershipProposal, action: 'accept' | 'decline') {
    setBusy(true); setMessage('');
    try {
      await ownershipConsentApi.respond(proposal, action);
      if (action === 'accept') await onAccepted?.();
      const result = await ownershipConsentApi.inbox();
      if (!result.complete) throw new Error('Incomplete inbox');
      setProposals(result.proposals);
      setMessage(action === 'accept' ? 'Ownership accepted.' : 'Proposal declined.');
    } catch { setProposals(null); setMessage('The decision was not confirmed. Reload before retrying.'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Ownership proposals" className="space-y-3">
    <Button variant="secondary" disabled={busy} onClick={load}>Review ownership proposals</Button>
    {message && <div role="status"><Text as="p" variant="body" tone="muted" >{message}</Text></div>}
    {proposals && <ul className="space-y-3">{proposals.length === 0 && <li>No ownership proposals.</li>}{proposals.map(proposal => <li key={proposal.proposal_id} className="app-card p-4 space-y-2">
      <p>{proposal.resource_type}: {proposal.resource_id}</p>
      <Text as="p" variant="body" tone="muted" >Accepting makes you responsible for this resource. Expires {new Date(proposal.expires_at).toLocaleString()}.</Text>
      {proposal.status === 'pending' && !proposal.expired && proposal.ownership_current ? <div className="flex gap-2">
        <Button disabled={busy} onClick={() => respond(proposal, 'accept')}>Accept ownership</Button>
        <Button variant="secondary" disabled={busy} onClick={() => respond(proposal, 'decline')}>Decline</Button>
      </div> : <p>{proposal.status}{proposal.expired ? ' · expired' : ''}{proposal.status === 'pending' && !proposal.ownership_current ? ' · owner changed; request a new proposal' : ''}</p>}
    </li>)}</ul>}
  </section>;
}
