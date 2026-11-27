import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attachmentAnalysisApi, ATTACHMENT_ANALYSIS_QUERY_KEY,
  type ModelConnection, type ModelProfile,
} from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Button } from "../../../components/ui/Button";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { useToast } from "../../../context/ToastContext";
import { FormDialog } from "../../../templates";
import { Text } from "../../../ui";
import { profileAssignmentLabel } from "./ModelAssignmentField";
import { ModelRoleAssignment } from "./ModelRoleAssignment";

type Draft =
  | { kind: "model"; profileId: string; revision: number }
  | { kind: "preferences"; enabled: boolean; revision: number };

export function AttachmentAnalysisConfiguration({ profiles, connections }: {
  profiles: ModelProfile[]; connections: ModelConnection[];
}) {
  const user = useAuthOptional()?.user?.id;
  const workspace = useScopeOptional()?.scope?.workspaceId;
  const cache = useQueryClient();
  const { showToast } = useToast();
  const key = [...ATTACHMENT_ANALYSIS_QUERY_KEY, user, workspace];
  const request = { headers: { "X-Integral-Scope": `ws:${workspace}` } };
  const query = useQuery({
    queryKey: key, queryFn: () => attachmentAnalysisApi.get(request), enabled: Boolean(user && workspace),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const save = useMutation({
    mutationFn: (body: Parameters<typeof attachmentAnalysisApi.save>[0]) => attachmentAnalysisApi.save(body, request),
    onSuccess: result => {
      cache.setQueryData(key, result);
      setDraft(null);
      showToast("Vision settings saved.", "success");
    },
    onError: () => { void query.refetch(); },
  });
  const data = query.data;
  if (!data) return <Text as="p" variant="body-sm" role={query.isError ? "alert" : "status"}>
    {query.isError ? <>Vision settings could not be loaded.{" "}
      <button className="underline" onClick={() => void query.refetch()}>Retry</button>
    </> : "Loading vision settings…"}
  </Text>;

  const current = draft?.revision === data.revision ? draft : null;
  const eligible = profiles.filter(profile => profile.supports_vision);
  const choices = eligible.map(profile => ({ id: profile.id, label: profileAssignmentLabel(profile, connections) }));
  const savedId = data.configuration.profile_id ?? "";
  const saved = eligible.find(profile => profile.id === savedId);
  const automatic = data.recommended_model ? `Automatic · ${data.recommended_model}` : "Automatic · compatible chat model";
  const activeModel = savedId ? saved?.model ?? "Selected model unavailable" : data.recommended_model || "Automatic";
  const modelDraft = current?.kind === "model" &&
    (current.profileId === "" || choices.some(option => option.id === current.profileId)) ? current : null;
  const preferences = current?.kind === "preferences" ? current : null;
  const canChooseModel = data.editable && data.can_choose_profile;
  const canEditPreferences = data.editable && (data.available || data.can_choose_profile);
  const error = save.isError ? errorMessageFromAxios(save.error, "Could not save vision settings.") : undefined;

  function cancel() {
    if (!save.isPending) { setDraft(null); save.reset(); }
  }

  return <section aria-label="Vision model assignment" className="space-y-3">
    <ModelRoleAssignment
      label="Vision model"
      active={data.available && data.configuration.enabled ? data.editable ? activeModel : "Workspace assignment" : "Off"}
      selected={modelDraft?.profileId ?? savedId}
      options={[
        { id: "", label: automatic },
        ...(savedId && !saved ? [{ id: savedId, label: "Selected model unavailable", disabled: true }] : []),
        ...choices,
      ]}
      secondary={canEditPreferences && <Button type="button" variant="ghost" size="sm" disabled={Boolean(modelDraft) || save.isPending}
        onClick={() => { save.reset(); setDraft({ kind: "preferences", enabled: data.configuration.enabled, revision: data.revision }); }}>
        Image processing preferences
      </Button>}
      dirty={Boolean(modelDraft)}
      pending={save.isPending}
      onChange={canChooseModel ? profileId => {
        save.reset();
        setDraft(profileId === savedId ? null : { kind: "model", profileId, revision: data.revision });
      } : undefined}
      onSave={() => {
        if (!modelDraft) return;
        save.mutate({ enabled: data.configuration.enabled, revision: modelDraft.revision, profile_id: modelDraft.profileId });
      }}
      onCancel={cancel}
      error={preferences ? undefined : error}
      helper={!data.available ? "Image analysis is unavailable on this deployment."
        : canChooseModel && !choices.length ? "Register a vision-capable model in Providers & Models to override Automatic." : undefined}
    />
    {preferences && <FormDialog open title="Image processing preferences" onClose={cancel}
      onSubmit={() => save.mutate({ enabled: preferences.enabled, revision: preferences.revision })}
      submitLabel="Save preferences" submitLoading={save.isPending} submitDisabled={preferences.enabled === data.configuration.enabled}>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={preferences.enabled} disabled={save.isPending}
          onChange={event => setDraft({ ...preferences, enabled: event.target.checked })} />
        <Text as="span" variant="body-sm">Automatically analyze image attachments</Text>
      </label>
      <Text as="p" variant="body-sm" tone="muted">
        Integral sends a resized image to the vision model to describe it and read visible text, including text on photographed or scanned documents uploaded as images.
        PDF and Office files use built-in text extraction. Scanned PDF pages are not sent to the vision model.
      </Text>
      {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
    </FormDialog>}
  </section>;
}
