import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { aiConfigurationApi } from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { KebabMenu } from "../../../components/ui/KebabMenu";
import { Brain } from "lucide-react";
import { useToast } from "../../../context/ToastContext";
import { LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import { useAIConfiguration } from "../../settings/hooks/useAIConfiguration";
import { useChatActivity } from "../AIChatSurface";

export function ConversationModelPicker() {
  const { activeThreadId, isRunning, activeProviderId } = useChatActivity();
  const configuration = useAIConfiguration();
  const qc = useQueryClient();
  const { showToast } = useToast();
  const key = [
    "conversation-model",
    activeThreadId,
    configuration.data?.effective.workspace_id,
  ];
  const selection = useQuery({
    queryKey: key,
    queryFn: () => aiConfigurationApi.conversation(activeThreadId!),
    enabled:
      !!activeThreadId &&
      activeProviderId === "integral_native" &&
      configuration.data?.effective.editable === true,
  });
  const change = useMutation({
    mutationFn: (id: string) =>
      aiConfigurationApi.conversation(
        activeThreadId!,
        id === "__heavy" || id === "" ? "" : id,
        id === "__heavy" ? "heavy" : id ? "profile" : "inherit",
      ),
    onSuccess: (result) => {
      qc.setQueryData(key, result);
    },
    onError: (error) =>
      showToast(
        errorMessageFromAxios(
          error,
          "Could not change the conversation model.",
        ),
        "error",
      ),
  });
  if (!selection.data?.editable || !configuration.data?.profiles.length)
    return null;
  const automatic =
    configuration.data.effective.routing?.mode === "light_heavy";
  const selected = configuration.data.profiles.find(
    (p) => p.id === selection.data.profile_id,
  );
  const description = selected
    ? `${selected.name} · ${selected.model}`
    : selection.data.routing_mode === "heavy"
      ? "Heavy model for this conversation"
      : automatic
        ? "Use the workspace Light and Heavy models"
        : "Use the workspace default model";
  const current =
    selection.data.profile_id ||
    (selection.data.routing_mode === "heavy" ? "__heavy" : "");
  const disabled = isRunning || change.isPending;
  return (
    <KebabMenu
      size="md"
      portal
      align="start"
      ariaLabel="Model for this conversation"
      title={description}
      menuTitle="Model for this conversation"
      menuCaption={change.isPending ? "Saving your selection…" : description}
      disabled={isRunning}
      icon={
        <Brain size={17} strokeWidth={LINE_ICON_STROKE} aria-hidden />
      }
      items={[
        {
          key: "workspace",
          label: automatic ? "Automatic · Light + Heavy" : "Workspace default",
          description: "Follow this workspace’s model configuration.",
          selected: current === "",
          disabled,
          onClick: () => change.mutate(""),
        },
        {
          key: "heavy",
          label: "Heavy only",
          description:
            configuration.data.effective.routing?.heavy_model ||
            configuration.data.effective.model ||
            "Use the main model for every request.",
          selected: current === "__heavy",
          disabled,
          onClick: () => change.mutate("__heavy"),
        },
        ...configuration.data.profiles.map((p, index) => ({
          key: p.id,
          label: p.name,
          description: `${p.model} · ${p.generation_settings.reasoning_effort ? `${p.generation_settings.reasoning_effort} reasoning` : "Default reasoning"}`,
          selected: current === p.id,
          dividerAbove: index === 0,
          disabled,
          onClick: () => change.mutate(p.id),
        })),
      ]}
    />
  );
}
