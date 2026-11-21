import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attachmentAnalysisApi,
  ATTACHMENT_ANALYSIS_QUERY_KEY,
} from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { useToast } from "../../../context/ToastContext";
import { Button } from "../../../components/ui/Button";
import { FormDialog } from "../../../templates";
import { Select, Text } from "../../../ui";

export function AttachmentAnalysisConfiguration() {
  const auth = useAuthOptional();
  const scope = useScopeOptional();
  const workspaceId = scope?.scope?.workspaceId;
  const client = useQueryClient();
  const { showToast } = useToast();
  const query = useQuery({
    queryKey: [...ATTACHMENT_ANALYSIS_QUERY_KEY, auth?.user?.id, workspaceId],
    queryFn: attachmentAnalysisApi.get,
    enabled: !!auth?.user && !!workspaceId,
  });
  const [draft, setDraft] = useState<{ enabled: boolean; revision: number } | null>(null);
  const save = useMutation({
    mutationFn: attachmentAnalysisApi.save,
    onSuccess: () => {
      setDraft(null);
      void client.invalidateQueries({
        queryKey: ATTACHMENT_ANALYSIS_QUERY_KEY,
      });
      showToast("Image processing updated.", "success");
    },
  });
  const data = query.data;
  if (!data)
    return (
      <Text as="p" variant="body-sm" tone="muted">
        {query.isError
          ? "Image processing settings are unavailable."
          : "Loading image processing settings…"}
      </Text>
    );
  const close = () => { if (!save.isPending) setDraft(null); };
  return (
    <section aria-label="Attachment analysis" className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <Text as="p" weight="medium">Image descriptions</Text>
        <Text as="p" variant="body-sm" tone="muted">
          {data.available && data.configuration.enabled
            ? "Automatic · uses your configured vision model"
            : "Off · file details only for images"}
        </Text>
        <Text as="p" variant="body-sm" tone="muted">
          Document text extraction runs independently.
        </Text>
        {!data.available && <Text as="p" variant="meta" tone="muted">
          Automatic descriptions are disabled by your deployment.
        </Text>}
      </div>
      {data.editable && data.available && <Button type="button" variant="secondary" size="sm"
        onClick={() => { save.reset(); setDraft({ enabled: data.configuration.enabled, revision: data.revision }); }}>
        Configure image descriptions
      </Button>}
      {draft && <FormDialog open title="Image descriptions" onClose={close}
        onSubmit={() => save.mutate(draft)} submitLabel="Save settings"
        submitDisabled={draft.enabled === data.configuration.enabled} submitLoading={save.isPending}>
        <label className="flex flex-col gap-2">
          <Text as="span" variant="body-sm">Processing mode</Text>
          <Select value={draft.enabled ? "automatic" : "off"} disabled={save.isPending}
            onChange={event => setDraft({ ...draft, enabled: event.target.value === "automatic" })}>
            <option value="automatic">Automatic · use a configured vision model</option>
            <option value="off">Off · file details only for images</option>
          </Select>
        </label>
        <Text as="p" variant="meta" tone="muted">
          Automatic descriptions send a resized image to your workspace’s vision model and label the result as AI-generated. Native document text extraction continues when this is off.
        </Text>
        {save.isError && <Text as="p" role="alert" tone="danger">
          {errorMessageFromAxios(save.error, "Could not update image processing.")}
        </Text>}
      </FormDialog>}
    </section>
  );
}
