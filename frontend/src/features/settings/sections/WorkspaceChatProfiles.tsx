import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { connectionProfilesApi, type ProfileSettings } from '../../../api/connectionProfiles';
import { Button } from '../../../components/ui/Button';
import { Field } from '../../../patterns';
import { Select, Text } from '../../../ui';

export function WorkspaceChatProfiles({ workspaceId }: { workspaceId: string }) {
  const cache = useQueryClient();
  const queryKey = ['connection-profiles', workspaceId];
  // The profile index already keeps this query mounted. Reopening the
  // assignment panel should not fetch again immediately after saving a profile.
  const query = useQuery({
    queryKey,
    queryFn: () => connectionProfilesApi.get(workspaceId),
    refetchOnMount: false,
  });
  const [selected, setSelected] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const settings = query.data;
  const assignments = settings?.native_chat_profiles ?? [];
  const published = Object.entries(settings?.profiles ?? {}).flatMap(([key, { published }]) =>
    published && (published.audience ?? (published.key === 'public' ? 'public' : 'staff')) === 'staff'
      ? [{ key, name: published.name }] : []);
  const available = published.filter(profile => !assignments.includes(profile.key));

  async function add(settings: ProfileSettings) {
    if (saving || !available.some(profile => profile.key === selected)) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const updated = await connectionProfilesApi.setNativeChatProfiles(
        workspaceId, settings.revision, [...assignments, selected],
      );
      cache.setQueryData(queryKey, updated);
      void cache.invalidateQueries({ queryKey: ['native-chat-profiles', workspaceId] });
      setSelected('');
      setMessage('Profile added to workspace chat.');
    } catch (failure) {
      const response = failure as { response?: { data?: { message?: string } } };
      setError(response.response?.data?.message || 'Could not add this profile. Refresh the list and try again.');
      void query.refetch();
    } finally {
      setSaving(false);
    }
  }

  return <section aria-label="Workspace chat profiles" className="flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-4">
    <Text as="h4" weight="semibold">Workspace chat profiles</Text>
    <Text as="p" variant="body-sm" tone="muted">Add a published workspace profile to the assistant picker. People still need workspace access. Existing conversations keep their assigned profile.</Text>
    <a href="/agent" className="self-start text-sm underline">Open workspace chat</a>
    {query.isPending ? <Text as="p" role="status">Loading profiles…</Text> : !settings
      ? <div><Text as="p" role="alert">Profiles could not be loaded.</Text><Button variant="secondary" onClick={() => void query.refetch()}>Retry</Button></div>
      : <>
        {assignments.length ? <ul className="list-disc pl-5">
          {assignments.map(key => <Text key={key} as="li" variant="body-sm">
            {published.find(profile => profile.key === key)?.name ?? 'Unavailable profile'}
          </Text>)}
        </ul> : <Text as="p" variant="body-sm">Only the default Workspace Assistant is available.</Text>}
        {available.length > 0 && <>
          <Field label="Published profile" htmlFor="workspace-chat-profile">
            <Select id="workspace-chat-profile" value={selected} disabled={saving} onChange={event => setSelected(event.target.value)}>
              <option value="">Choose a profile</option>
              {available.map(profile => <option value={profile.key} key={profile.key}>{profile.name}</option>)}
            </Select>
          </Field>
          <div><Button disabled={saving || !available.some(profile => profile.key === selected)} onClick={() => void add(settings)}>
            {saving ? 'Adding…' : 'Add to workspace chat'}
          </Button></div>
        </>}
      </>}
    {message && <Text as="p" role="status" variant="body-sm">{message}</Text>}
    {error && <Text as="p" role="alert" variant="body-sm" tone="danger">{error}</Text>}
  </section>;
}
