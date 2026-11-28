import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  voiceConfigurationApi,
  VOICE_CONFIGURATION_QUERY_KEY,
  type VoiceChoice,
  type ModelConnection,
  type ModelProfile,
} from "../../../api/aiConfiguration";
import { SPEECH_CONFIG_QUERY_ROOT } from "../../../api/speech";
import { useScopeOptional } from "../../../context/ScopeContext";
import { useAuthOptional } from "../../../context/AuthContext";
import { useToast } from "../../../context/ToastContext";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Button } from "../../../components/ui/Button";
import { Text } from "../../../ui";
import { ModelRoleAssignment } from "./ModelRoleAssignment";
import { voiceRoleOptions } from "./voiceRoleModels";

type Props = {
  connections: ModelConnection[];
  profiles?: ModelProfile[];
  embedded?: boolean;
  secondaryAction?: ReactNode;
  navigateToSection?: (id: string) => void;
};

export function VoiceConfiguration(props: Props) {
  const workspace = useScopeOptional()?.scope?.workspaceId;
  const user = useAuthOptional()?.user?.id;
  return <ScopedVoiceConfiguration key={JSON.stringify([workspace, user])} workspace={workspace} user={user} {...props} />;
}

function ScopedVoiceConfiguration({
  workspace, user, connections, profiles = [], navigateToSection, embedded = false, secondaryAction,
}: Props & { workspace?: string; user?: string }) {
  const qc = useQueryClient();
  const { showToast } = useToast();
  const [draft, setDraft] = useState<{ id: string; revision: number } | null>(null);
  const key = [...VOICE_CONFIGURATION_QUERY_KEY, user, workspace];
  const request = { headers: { "X-Integral-Scope": `ws:${workspace}` } };
  const query = useQuery({ queryKey: key, queryFn: () => voiceConfigurationApi.get(request), enabled: Boolean(user && workspace) });
  const save = useMutation({
    mutationFn: (body: VoiceChoice & { revision: number }) => voiceConfigurationApi.save(body, request),
    onSuccess: result => {
      qc.setQueryData(key, result);
      setDraft(null);
      void qc.invalidateQueries({ queryKey: SPEECH_CONFIG_QUERY_ROOT });
      showToast("Voice model saved.", "success");
    },
    onError: () => { void query.refetch(); },
  });
  const data = query.data;
  const { options, selected } = voiceRoleOptions(connections, profiles, "transcription", data?.configuration);
  const currentDraft = draft?.revision === data?.revision ? draft : null;
  const selectedModel = options.find(item => item.id === currentDraft?.id && item.id !== "legacy");
  const dirty = Boolean(currentDraft && (selectedModel || currentDraft.id === "inherit" || currentDraft.id === "disabled"));
  function submit() {
    if (!currentDraft) return;
    if (selectedModel) {
      save.mutate({ mode: "provider", model: selectedModel.model, connection_id: selectedModel.connectionId, revision: currentDraft.revision });
    } else if (currentDraft.id === "inherit" || currentDraft.id === "disabled") {
      save.mutate({ mode: currentDraft.id, model: "", connection_id: "", revision: currentDraft.revision });
    }
  }
  return <section aria-label="Speech to text assignment" className={embedded ? "space-y-3" : "space-y-3 border-t border-[var(--border-subtle)] pt-5"}>
    {query.isError && <Text as="p" role="alert" variant="body-sm">
      Transcription settings could not be loaded.{" "}
      <button className="underline" onClick={() => void query.refetch()}>Retry</button>
    </Text>}
    {data && <ModelRoleAssignment
      label="Transcription model"
      active={data.effective_model || "Browser recognition when available"}
      secondary={secondaryAction ?? (navigateToSection && <Button type="button" variant="ghost" size="sm" onClick={() => navigateToSection("voice-input")}>
        Voice input preferences
      </Button>)}
      options={[
        { id: "inherit", label: "Deployment default" },
        { id: "disabled", label: "Browser recognition only" },
        ...options,
      ]}
      selected={dirty && currentDraft ? currentDraft.id : selected}
      dirty={dirty}
      pending={save.isPending}
      onChange={data.editable ? id => {
        save.reset();
        setDraft(id === selected ? null : { id, revision: data.revision });
      } : undefined}
      onSave={submit}
      onCancel={() => { setDraft(null); save.reset(); }}
      error={save.isError ? errorMessageFromAxios(save.error, "Could not save transcription settings.") : undefined}
      helper={data.editable && (options.some(option => option.id !== "legacy")
        ? "Provider-backed dictation sends audio to the assigned provider."
        : "Register a compatible transcription model to assign it here.")}
    />}
    {!data && secondaryAction && <div className="flex justify-end">{secondaryAction}</div>}
  </section>;
}
