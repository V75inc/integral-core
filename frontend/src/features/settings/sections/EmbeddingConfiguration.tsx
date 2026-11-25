import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  embeddingConfigurationApi,
  EMBEDDING_CONFIGURATION_QUERY_KEY,
  type EmbeddingChoice,
  type ModelConnection,
  type ModelProfile,
} from "../../../api/aiConfiguration";
import { ModelAssignmentField, profileAssignmentLabel } from "./ModelAssignmentField";
import { isEmbeddingProfile } from "../../../api/modelProfileKinds";
import { useScopeOptional } from "../../../context/ScopeContext";
import { useAuthOptional } from "../../../context/AuthContext";
import { useToast } from "../../../context/ToastContext";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Button } from "../../../components/ui/Button";
import { Modal } from "../../../components/ui";
import { Input, Text } from "../../../ui";

const fresh = (): EmbeddingChoice => ({
  mode: "deployment",
  connection_id: "",
  model: "",
  dimensions: 1536,
  allow_workspace_text: false,
});
const examples: Record<string, { model: string; dimensions: number }> = {
  gemini: { model: "gemini-embedding-001", dimensions: 768 },
  openai: { model: "text-embedding-3-small", dimensions: 1536 },
  openrouter: { model: "openai/text-embedding-3-small", dimensions: 1536 },
  ollama_local: { model: "embeddinggemma", dimensions: 768 },
};

export function EmbeddingConfiguration({
  connections,
  embedded = false,
  profiles = [],
  recommendedProfile,
}: {
  connections: ModelConnection[];
  embedded?: boolean;
  profiles?: ModelProfile[];
  recommendedProfile?: ModelProfile;
}) {
  const scope = useScopeOptional();
  const auth = useAuthOptional();
  const workspaceId = scope?.scope?.workspaceId;
  const qc = useQueryClient();
  const request = { headers: { "X-Integral-Scope": `ws:${workspaceId}` } };
  const { showToast } = useToast();
  const [draft, setDraft] = useState<
    (EmbeddingChoice & { revision: number }) | null
  >(null);
  const [continuePrevious, setContinuePrevious] = useState(false);
  useEffect(() => {
    setDraft(null);
    setContinuePrevious(false);
  }, [workspaceId, auth?.user?.id]);
  const query = useQuery({
    queryKey: [
      ...EMBEDDING_CONFIGURATION_QUERY_KEY,
      auth?.user?.id,
      workspaceId,
    ],
    queryFn: () => embeddingConfigurationApi.get(request),
    enabled: !!auth?.user && !!workspaceId,
    refetchInterval: (q) =>
      q.state.data?.rebuild_status &&
      !["ready", "cancelled", "superseded"].includes(
        q.state.data.rebuild_status,
      )
        ? 15000
        : false,
  });
  const save = useMutation({
    mutationFn: (choice: Parameters<typeof embeddingConfigurationApi.save>[0]) => embeddingConfigurationApi.save(choice, request),
    onSuccess: (result) => {
      setDraft(null);
      void qc.invalidateQueries({
        queryKey: EMBEDDING_CONFIGURATION_QUERY_KEY,
      });
      showToast(
        result.changed === false
          ? "Embedding model saved."
          : "Embedding model saved. Search indexing will rebuild in the background.",
        "success",
      );
    },
    onError: (error) =>
      showToast(
        errorMessageFromAxios(error, "Could not save embedding model."),
        "error",
      ),
  });
  const test = useMutation({
    mutationFn: (choice: EmbeddingChoice) => embeddingConfigurationApi.test(choice, request),
    onSuccess: (result) =>
      showToast(result.message, result.valid ? "success" : "error"),
    onError: (error) =>
      showToast(
        errorMessageFromAxios(error, "Embedding model test failed."),
        "error",
      ),
  });
  const cancelChange = useMutation({
    mutationFn: (revision: number) => embeddingConfigurationApi.cancel(revision, request),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: EMBEDDING_CONFIGURATION_QUERY_KEY,
      });
      showToast("Model change cancelled.", "success");
    },
    onError: (error) =>
      showToast(
        errorMessageFromAxios(error, "Could not cancel the model change."),
        "error",
      ),
  });
  const supported = connections.filter((c) => c.provider !== "anthropic");
  const data = query.data;
  const eligibleProfiles = profiles.filter(
    (profile) => isEmbeddingProfile(profile) && supported.some((c) => c.id === profile.connection_id),
  );
  const configuredProfile = eligibleProfiles.find(
    (profile) => profile.connection_id === data?.configuration?.connection_id && profile.model === data?.configuration?.model,
  );
  const suggested = data?.editable && data.provider_overrides_supported && data.revision === 0 &&
    data.configuration?.mode !== "provider" && eligibleProfiles.find(p => p.id === recommendedProfile?.id);
  const selection = data?.configuration?.mode === "provider"
    ? configuredProfile?.id ?? "legacy"
    : suggested ? suggested.id : "deployment";
  const openSettings = (profileId = selection) => {
    if (!data) return;
    setContinuePrevious(false);
    const profile = eligibleProfiles.find((item) => item.id === profileId);
    const choice = { ...fresh(), ...data.configuration, revision: data.revision };
    if (profile) {
      const provider = connections.find(c => c.id === profile.connection_id)?.provider;
      const suggested = provider && examples[provider];
      const unchanged = choice.mode === "provider" && choice.connection_id === profile.connection_id && choice.model === profile.model;
      setDraft({
        ...choice,
        mode: "provider",
        connection_id: profile.connection_id,
        model: profile.model,
        // Prefill only the reviewed provider/model pair. Saving still requires consent.
        dimensions: unchanged ? choice.dimensions : suggested?.model === profile.model ? suggested.dimensions : 0,
        allow_workspace_text: unchanged && choice.allow_workspace_text,
      });
    } else {
      setDraft(profileId === "deployment" ? { ...fresh(), revision: data.revision } : choice);
    }
  };
  const localLabel =
    data?.processing_location === "device"
      ? "On this device"
      : "On this server";
  const busy = save.isPending || test.isPending || cancelChange.isPending;
  const valid =
    !!draft &&
    (draft.mode === "deployment" ||
      (!!draft.connection_id &&
        !!draft.model.trim() &&
        draft.allow_workspace_text &&
        Number.isInteger(draft.dimensions) &&
        draft.dimensions >= 16 &&
        draft.dimensions <= 2000));
  return (
    <section
      aria-label="Embedding model assignment"
      className={embedded ? "space-y-2" : "space-y-2 border-t border-[var(--border-subtle)] pt-5"}
    >
      {data && (
        <div className="space-y-2">
          <ModelAssignmentField
            label="Embedding model"
            value={selection}
            active={data.effective_model ? `${data.effective_model} · ${data.dimensions} dimensions` : undefined}
            disabled={!data.editable || busy}
            onChange={openSettings}
            secondary={data.editable && data.provider_overrides_supported && selection !== "deployment" && <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => openSettings()}>
              Dimensions and consent
            </Button>}
            options={<>
              <option value="deployment">{localLabel} · default</option>
              {selection === "legacy" && (
                <option value="legacy">{data.configuration?.model} · current configuration</option>
              )}
              {eligibleProfiles.map(profile => (
                <option key={profile.id} value={profile.id} disabled={!data.provider_overrides_supported}>
                  {profileAssignmentLabel(profile, connections)}{suggested && suggested.id === profile.id ? " · awaiting consent" : ""}
                </option>
              ))}
            </>}
          />
          {data.editable && data.provider_overrides_supported && !eligibleProfiles.length && (
            <Text as="p" variant="meta" tone="muted">
              Add an encoder in Providers &amp; Models to choose it here.
            </Text>
          )}
        </div>
      )}
      {data?.rebuild_status && (
        <Text as="p" variant="meta" tone="muted">
          {data.rebuild_status === "ready"
            ? "Index ready."
            : data.rebuild_status === "superseded"
              ? "Earlier rebuild replaced by newer settings."
              : data.rebuild_status === "cancelled"
                ? "Model change cancelled."
                : data.rebuild_phase === "catching_up"
                  ? "Finishing recent changes before switching models."
                  : data.rebuild_phase === "verifying"
                    ? data.editable
                      ? `Verifying search coverage · ${(data.verified ?? 0).toLocaleString()} records checked.`
                      : "Verifying search coverage."
                    : data.editable
                      ? `Preparing search · ${(data.enumerated ?? 0).toLocaleString()} records reviewed.`
                      : "Preparing search."}
        </Text>
      )}
      {data?.rebuild_error && (
        <Text as="p" variant="body-sm" tone="muted">
          {data.rebuild_error}
        </Text>
      )}
      {data?.pending_model && (
        <div className="flex flex-wrap items-center gap-2">
          <Text variant="body-sm" tone="muted">
            Preparing {data.pending_model}.{" "}
            {data.serving_available !== false
              ? "The current model remains available."
              : "Keyword search remains available."}
          </Text>
          {data.editable && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => cancelChange.mutate(data.revision)}
            >
              Cancel change
            </Button>
          )}
        </div>
      )}
      {data?.error && (
        <Text as="p" variant="body-sm" tone="muted">
          {data.error}
        </Text>
      )}
      {query.isError && (
        <Text as="p" variant="body-sm" tone="muted">
          Embedding settings could not be loaded.{" "}
          <button
            className="underline"
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </Text>
      )}
      {data && !data.provider_overrides_supported && (
        <Text as="p" variant="meta" tone="muted">
          Provider embeddings require a PostgreSQL passage index. This
          installation uses its deployment default.
        </Text>
      )}
      {data && !data.editable && (
        <Text as="p" variant="meta" tone="muted">
          Managed by the workspace owner or deployment administrator.
        </Text>
      )}
      <Modal
        open={!!draft}
        onClose={() => {
          if (!busy) setDraft(null);
        }}
        title={draft?.mode === "deployment" ? "Use the deployment default" : "Dimensions and consent"}
      >
        {draft && (
          <>
          <Modal.Body>
            <Text as="p" variant="body-sm" weight="medium">
              {draft.mode === "provider" ? [draft.model, connections.find(c => c.id === draft.connection_id)?.name].filter(Boolean).join(" · ") : localLabel}
            </Text>
            {draft.mode === "provider" && (
              <>
                <Text
                  as="label"
                  variant="body-sm"
                  className="flex flex-col gap-1"
                >
                  Vector dimensions
                  <Input
                    type="number"
                    min={16}
                    max={2000}
                    value={draft.dimensions || ""}
                    disabled={busy}
                    onChange={(e) =>
                      setDraft({ ...draft, dimensions: Number(e.target.value) })
                    }
                  />
                  <Text variant="meta" tone="muted">
                    Must match the model output. Supported range: 16–2,000.
                  </Text>
                </Text>
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={draft.allow_workspace_text}
                    disabled={busy}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        allow_workspace_text: e.target.checked,
                      })
                    }
                    className="mt-1"
                  />
                  <Text variant="body-sm">
                    Allow indexed workspace text, selected attachment text and
                    search queries to be processed by this provider. External
                    services receive this text.
                  </Text>
                </label>
              </>
            )}
            <Text as="p" variant="meta" tone="muted">
              Changing models rebuilds the search index in a separate vector
              space. The new model starts serving only after coverage is
              verified. Testing sends a sample phrase only.
            </Text>
            {data?.effective_provider &&
              !["local", "unavailable"].includes(data.effective_provider) && (
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={continuePrevious}
                    disabled={busy}
                    onChange={(e) => setContinuePrevious(e.target.checked)}
                    className="mt-1"
                  />
                  <Text variant="body-sm">
                    Keep using the current provider until the new model is
                    ready. These calls may incur charges. Otherwise, search uses
                    existing keyword results during the change.
                  </Text>
                </label>
              )}
          </Modal.Body>
          <Modal.Footer>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => setDraft(null)}
              >
                Cancel
              </Button>
              <Button
                variant="secondary"
                disabled={busy || !valid}
                onClick={() => {
                  const { revision: _revision, ...choice } = draft;
                  test.mutate(choice);
                }}
              >
                {test.isPending ? "Testing…" : "Test model"}
              </Button>
              <Button
                disabled={busy || !valid}
                onClick={() =>
                  save.mutate({
                    ...draft,
                    continue_previous_provider: continuePrevious,
                  })
                }
              >
                {save.isPending ? "Saving…" : "Save model"}
              </Button>
          </Modal.Footer>
          </>
        )}
      </Modal>
    </section>
  );
}
