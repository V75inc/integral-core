import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  REALTIME_VOICE_QUERY_KEY,
  realtimeVoiceConfigurationApi,
  type ModelConnection,
  type ModelProfile,
  type RealtimeVoiceChoice,
} from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { Text } from "../../../ui";
import { VoiceRoleAssignment } from "./VoiceRoleAssignment";
import { isRealtimeVoiceModel, voiceRoleOptions } from "./voiceRoleModels";

type Props = { connections: ModelConnection[]; profiles?: ModelProfile[] };

export function RealtimeVoiceConfiguration(props: Props) {
  const workspace = useScopeOptional()?.scope?.workspaceId;
  const user = useAuthOptional()?.user?.id;
  return <ScopedRealtimeVoiceConfiguration key={JSON.stringify([workspace, user])} workspace={workspace} user={user} {...props} />;
}

function ScopedRealtimeVoiceConfiguration({ workspace, user, connections, profiles = [] }: Props & {
  workspace?: string; user?: string;
}) {
  const cache = useQueryClient();
  const key = [...REALTIME_VOICE_QUERY_KEY, user, workspace];
  const request = { headers: { "X-Integral-Scope": `ws:${workspace}` } };
  const query = useQuery({ queryKey: key, queryFn: () => realtimeVoiceConfigurationApi.get(request), enabled: Boolean(user && workspace) });
  const [draft, setDraft] = useState<{ id: string; revision: number } | null>(null);
  const save = useMutation({
    mutationFn: (body: RealtimeVoiceChoice & { revision: number }) => realtimeVoiceConfigurationApi.save(body, request),
    onSuccess: result => { cache.setQueryData(key, result); setDraft(null); },
    onError: () => { void query.refetch(); },
  });
  const data = query.data;
  const { options, selected } = voiceRoleOptions(connections, profiles, "realtime", data?.configuration);
  const currentDraft = draft?.revision === data?.revision ? draft : null;
  const selectedModel = options.find(option => option.id === currentDraft?.id && option.id !== "legacy");
  const dirty = Boolean(currentDraft && (selectedModel || currentDraft.id === "disabled"));
  function submit() {
    if (!currentDraft) return;
    if (selectedModel && isRealtimeVoiceModel(selectedModel.model)) {
      save.mutate({ mode: "provider", connection_id: selectedModel.connectionId, model: selectedModel.model, revision: currentDraft.revision });
    } else if (currentDraft.id === "disabled") {
      save.mutate({ mode: "disabled", connection_id: "", model: "", revision: currentDraft.revision });
    }
  }
  return <section aria-label="Live voice assignment" className="space-y-3">
    <div className="space-y-1">
      <Text as="h3" weight="medium">Live voice calls</Text>
      {data && <Text as="p" variant="body-sm" className="break-words">Active: {data.effective_model || "Off"}</Text>}
      {query.isError && <Text as="p" role="alert" variant="body-sm">
        Live voice settings could not be loaded.{" "}
        <button className="underline" onClick={() => void query.refetch()}>Retry</button>
      </Text>}
    </div>
    {data?.editable && <VoiceRoleAssignment
      label="Realtime model"
      options={[{ id: "disabled", label: "Off" }, ...options]}
      selected={dirty && currentDraft ? currentDraft.id : selected}
      dirty={dirty}
      pending={save.isPending}
      onChange={id => {
        save.reset();
        setDraft(id === selected ? null : { id, revision: data.revision });
      }}
      onSave={submit}
      onCancel={() => { setDraft(null); save.reset(); }}
      error={save.isError ? errorMessageFromAxios(save.error, "Could not save live voice settings.") : undefined}
      helper={options.some(option => option.id !== "legacy")
        ? "Call audio is processed by the assigned provider."
        : "Register a compatible realtime model to assign it here."}
    />}
  </section>;
}
