import { useId, useState } from 'react';

import type { ManagedChannelProfileConfiguration } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Select, Text } from '../../../ui';

export function ManagedChannelProfileAssignment({ configuration, locked, onSave }: {
  configuration: ManagedChannelProfileConfiguration;
  locked: boolean;
  onSave: (key: string | null) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(configuration.profile_key ?? '');
  const choices = configuration.eligible_profiles;
  const eligible = (key: string) => choices.some(choice => choice.key === key);
  const orphaned = configuration.profile_key !== null && !eligible(configuration.profile_key);
  const canSave = !locked && (draft || null) !== configuration.profile_key && (draft === '' || eligible(draft));

  return (
    <form className="flex flex-col items-start gap-2" onSubmit={event => {
      event.preventDefault();
      if (canSave) onSave(draft || null);
    }}>
      <Text as="label" htmlFor={id} variant="body-sm" weight="medium">Published public profile</Text>
      <Select id={id} value={draft} onChange={event => setDraft(event.target.value)}
        aria-describedby={`${id}-help`} disabled={locked}>
        <option value="">No profile assigned</option>
        {orphaned && <option value={configuration.profile_key ?? ''} disabled>Current profile is no longer eligible</option>}
        {choices.map(choice => <option key={choice.key} value={choice.key}>{choice.name}</option>)}
      </Select>
      <Text id={`${id}-help`} as="p" variant="body-sm" tone="muted">
        {!configuration.editable
          ? 'The profile assignment cannot be changed in the current channel state.'
          : choices.length === 0
            ? 'No eligible published public profiles are available.'
            : 'Saving a profile assignment does not enable the channel.'}
      </Text>
      <Button type="submit" variant="secondary" size="sm" disabled={!canSave}>Save profile</Button>
    </form>
  );
}
