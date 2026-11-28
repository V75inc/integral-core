import { useId, useState } from 'react';

import type { ManagedChannelProfileConfiguration } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Select, Text } from '../../../ui';
import './managedChannelProfileAssignment.css';

export function ManagedChannelProfileAssignment({ configuration, locked, onSave, compact = false, active = false, value, onDraft }: {
  configuration: ManagedChannelProfileConfiguration;
  compact?: boolean;
  active?: boolean;
  value?: string;
  onDraft?: (value: string) => void;
  locked: boolean;
  onSave: (key: string | null) => void;
}) {
  const id = useId();
  const [localDraft, setLocalDraft] = useState(configuration.profile_key ?? '');
  const [lockAttempt, setLockAttempt] = useState(0);
  const activeLock = compact && active && locked;
  const draft = value ?? localDraft;
  const setDraft = onDraft ?? setLocalDraft;
  const choices = configuration.eligible_profiles;
  const eligible = (key: string) => choices.some(choice => choice.key === key);
  const orphaned = configuration.profile_key !== null && !eligible(configuration.profile_key);
  const canSave = !locked && (draft || null) !== configuration.profile_key && (draft === '' || eligible(draft));

  return (
    <form className="flex flex-col items-start gap-2" onSubmit={event => {
      event.preventDefault();
      if (!compact && canSave) onSave(draft || null);
    }}>
      <Text as="label" htmlFor={id} variant="body-sm" weight="medium">Published agent profile</Text>
      <div className="relative w-full">
        <Select id={id} value={draft} onChange={event => setDraft(event.target.value)}
          aria-describedby={`${id}-help`} disabled={locked}>
          <option value="">No profile assigned</option>
          {orphaned && <option value={configuration.profile_key ?? ''} disabled>Current profile is no longer eligible</option>}
          {choices.map(choice => <option key={choice.key} value={choice.key}>{choice.name}</option>)}
        </Select>
        {activeLock && <button type="button" aria-disabled="true" aria-label="Published agent profile is locked"
          aria-describedby={`${id}-help`} className="absolute inset-0 cursor-not-allowed rounded-[var(--radius-input)] focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]"
          onClick={() => setLockAttempt(attempt => attempt + 1)} />}
      </div>
      <div id={`${id}-help`}>
        <Text key={activeLock ? lockAttempt : 0} as="p" variant="body-sm" tone="muted"
          className={activeLock && lockAttempt > 0 ? 'managed-profile-lock-feedback' : undefined}>
          {activeLock ? 'Disable this connector to select a different agent profile'
            : !configuration.editable ? 'The profile assignment cannot be changed in the current channel state.'
            : choices.length > 0 && 'Saving a profile assignment does not enable the channel.'}
        </Text>
        {choices.length === 0 && <Text as="p" variant="body-sm" tone="muted">No eligible published public profiles are available.</Text>}
        {compact && choices.length === 1 && <Text as="p" variant="body-sm" tone="muted">Only one published agent profile is eligible in this workspace.</Text>}
      </div>
      {!compact && <Button type="submit" variant="secondary" size="sm" disabled={!canSave}>Save profile</Button>}
    </form>
  );
}
