import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { aiConfigurationApi } from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Select, Text } from "../../../ui";
import { useAIConfiguration } from "../../settings/hooks/useAIConfiguration";
import { useChatActivity } from "../AIChatSurface";

export function ConversationModelPicker() {
  const { activeThreadId, isRunning, activeProviderId } = useChatActivity();
  const configuration = useAIConfiguration();
  const qc = useQueryClient();
  const [failure, setFailure] = useState("");
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
      setFailure("");
      qc.setQueryData(key, result);
    },
    onError: (error) =>
      setFailure(
        errorMessageFromAxios(
          error,
          "Could not change the conversation model.",
        ),
      ),
  });
  if (!selection.data?.editable || !configuration.data?.profiles.length)
    return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-2">
      <Text
        as="label"
        variant="body-sm"
        tone="muted"
        className="flex items-center gap-2"
      >
        This conversation
        <Select
          aria-label="Model for this conversation"
          value={
            selection.data.profile_id ||
            (selection.data.routing_mode === "heavy" ? "__heavy" : "")
          }
          disabled={isRunning || change.isPending}
          onChange={(e) => change.mutate(e.target.value)}
        >
          <option value="">Use workspace routing</option>
          <option value="__heavy">Heavy only</option>
          {configuration.data.profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.model}
              {p.generation_settings.reasoning_effort
                ? ` · ${p.generation_settings.reasoning_effort}`
                : ""}
            </option>
          ))}
        </Select>
      </Text>
      {failure && (
        <div role="alert">
          <Text variant="body-sm" tone="danger">
            {failure}
          </Text>
        </div>
      )}
    </div>
  );
}
