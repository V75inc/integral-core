import { useMutation, useQueryClient } from "@tanstack/react-query";

import { connectionProfilesApi, publicWebChatSettingsKey, type PublicWebChatSettings } from "../../../api/connectionProfiles";
import { Button } from "../../../components/ui/Button";
import { Modal } from "../../../components/ui/Modal";
import { Select, Text } from "../../../ui";

type Change =
  | { kind: "assignment"; key: string | null }
  | { kind: "enablement"; enabled: boolean };

export function WebChatConnectorSettings({ workspaceId, principalId, settings, refreshing, onClose }: {
  workspaceId: string;
  principalId: string;
  settings: PublicWebChatSettings;
  refreshing: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const queryKey = [...publicWebChatSettingsKey(workspaceId), principalId];
  const mutation = useMutation({
    mutationFn: (change: Change) => change.kind === "assignment"
      ? connectionProfilesApi.assignPublicWebChat(workspaceId, settings.revision, change.key)
      : connectionProfilesApi.setPublicWebChatEnabled(workspaceId, settings.revision, change.enabled),
    retry: false,
    onSuccess: data => { qc.setQueryData(queryKey, data); },
    onSettled: () => qc.invalidateQueries({ queryKey, exact: true }),
  });
  const locked = refreshing || mutation.isPending;
  const eligible = settings.eligible_profiles.some(profile => profile.key === settings.profile_key);
  return (
    <Modal open title="Configure Web Chat" onClose={onClose}>
      <div className="flex flex-col gap-4 p-4">
        <Text as="p">Choose a published public profile without tools. Saving the assignment does not enable the chat.</Text>
        <form key={settings.revision} className="flex flex-col gap-3" onSubmit={event => {
          event.preventDefault();
          if (locked || settings.enabled) return;
          const key = new FormData(event.currentTarget).get("profile");
          if (key !== "" && !settings.eligible_profiles.some(profile => profile.key === key)) return;
          const next = typeof key === "string" && key !== "" ? key : null;
          if (next !== settings.profile_key) mutation.mutate({ kind: "assignment", key: next });
        }}>
          <Text as="label" variant="body-sm" weight="medium">
            Published public profile
            <Select name="profile" defaultValue={eligible ? settings.profile_key ?? "" : ""}
              disabled={locked || settings.enabled}>
              <option value="">No profile assigned</option>
              {settings.eligible_profiles.map(profile => (
                <option key={profile.key} value={profile.key}>{profile.name}</option>
              ))}
            </Select>
          </Text>
          <Button type="submit" variant="secondary" size="sm" disabled={locked || settings.enabled}>
            Save profile
          </Button>
        </form>
        {settings.enabled && <Text as="p" variant="body-sm" tone="muted">Disable Web Chat before changing the assigned profile.</Text>}
        {mutation.isError && <Text as="p" role="alert" tone="danger">Could not save Web Chat settings. Review the current settings and try again.</Text>}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant={settings.enabled ? "secondary" : "primary"} size="sm"
            disabled={locked || (!settings.enabled && !eligible)}
            onClick={() => { if (!locked) mutation.mutate({ kind: "enablement", enabled: !settings.enabled }); }}>
            {settings.enabled ? "Disable Web Chat" : "Enable Web Chat"}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>Close</Button>
        </div>
      </div>
    </Modal>
  );
}
