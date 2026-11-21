import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EmbeddingConfiguration } from "./EmbeddingConfiguration";
import { VoiceConfiguration } from "./VoiceConfiguration";
import { RealtimeVoiceConfiguration } from "./RealtimeVoiceConfiguration";
import {
  embeddingConfigurationApi,
  realtimeVoiceConfigurationApi,
  voiceConfigurationApi,
} from "../../../api/aiConfiguration";
const toast = vi.hoisted(() => vi.fn());
vi.mock("../../../context/ToastContext", () => ({
  useToast: () => ({ showToast: toast }),
}));
vi.mock("../../../context/AuthContext", () => ({
  useAuthOptional: () => ({ user: { id: "owner" } }),
}));
vi.mock("../../../context/ScopeContext", () => ({
  useScopeOptional: () => ({ scope: { workspaceId: "workspace" } }),
}));
vi.mock("../../../api/aiConfiguration", async (original) => ({
  ...(await original<typeof import("../../../api/aiConfiguration")>()),
  embeddingConfigurationApi: { get: vi.fn(), save: vi.fn(), test: vi.fn() },
  voiceConfigurationApi: { get: vi.fn(), save: vi.fn() },
  realtimeVoiceConfigurationApi: { get: vi.fn(), save: vi.fn() },
}));
const connections = [
  {
    id: "openai",
    name: "Saved OpenAI",
    provider: "openai" as const,
    base_url: "",
    key_fingerprint: "",
    validated_at: "",
    updated_at: "",
  },
  {
    id: "claude",
    name: "Saved Claude",
    provider: "anthropic" as const,
    base_url: "",
    key_fingerprint: "",
    validated_at: "",
    updated_at: "",
  },
];
const profiles = [
  ["transcribe", "openai", "gpt-4o-mini-transcribe", "STT model"],
  ["live", "openai", "gpt-realtime-2.1-mini", "Live model"],
  ["chat", "openai", "gpt-6.1-sol", "Chat model"],
  ["wrong-provider", "claude", "gpt-4o-mini-transcribe", "Non-OpenAI model"],
].map(([id, connection_id, model, name]) => ({ id, connection_id, model, name, updated_at: "", generation_settings: {} }));
const config = {
  workspace_id: "workspace",
  editable: true,
  provider_overrides_supported: true,
  revision: 4,
  configuration: {
    mode: "deployment" as const,
    connection_id: "",
    model: "",
    dimensions: 1536,
    allow_workspace_text: false,
  },
  effective_provider: "local",
  effective_model: "all-MiniLM-L6-v2",
  dimensions: 384,
  profile_id: "local",
  rebuild_status: "",
  error: "",
};
function show(node: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {node}
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(embeddingConfigurationApi.get).mockResolvedValue(config);
  vi.mocked(realtimeVoiceConfigurationApi.get).mockResolvedValue({
    editable: true, revision: 6, configuration: { mode: "disabled" }, effective_model: "",
  });
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
    workspace_id: "workspace",
    editable: true,
    revision: 2,
    configuration: { mode: "inherit", model: "", connection_id: "" },
    effective_model: "",
    effective_provider: "",
  });
});
it("keeps local default and requires explicit text consent for a saved embedding provider", async () => {
  show(<EmbeddingConfiguration connections={connections} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Configure embeddings" }),
  );
  fireEvent.change(screen.getByLabelText("Model source"), {
    target: { value: "provider" },
  });
  fireEvent.change(screen.getByLabelText("Provider"), {
    target: { value: "openai" },
  });
  expect(
    screen.queryByRole("option", { name: /Saved Claude/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save model" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  vi.mocked(embeddingConfigurationApi.test).mockResolvedValue({
    valid: true,
    message: "Test passed",
  });
  fireEvent.click(screen.getByRole("button", { name: "Test model" }));
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith("Test passed", "success"),
  );
  expect(vi.mocked(embeddingConfigurationApi.test).mock.calls[0][0]).toEqual(
    expect.objectContaining({
      model: "text-embedding-3-small",
      allow_workspace_text: true,
    }),
  );
  vi.mocked(embeddingConfigurationApi.save).mockRejectedValue(
    new Error("Unavailable"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save model" }));
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(expect.any(String), "error"),
  );
  expect(screen.getByLabelText("Embedding model ID")).toHaveValue(
    "text-embedding-3-small",
  );
  expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
});
it("shows an accurate provider restriction for unsupported search stores", async () => {
  vi.mocked(embeddingConfigurationApi.get).mockResolvedValue({
    ...config,
    provider_overrides_supported: false,
  });
  show(<EmbeddingConfiguration connections={connections} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Configure embeddings" }),
  );
  expect(screen.getByRole("option", { name: "Saved provider" })).toBeDisabled();
});
it("assigns a registered transcription model inline without collecting another API key", async () => {
  const navigate = vi.fn();
  show(<VoiceConfiguration connections={connections} profiles={profiles} navigateToSection={navigate} />);
  const model = await screen.findByRole("combobox", { name: "Transcription model" });
  expect(model).toHaveValue("inherit");
  expect(screen.getByRole("option", { name: /STT model/ })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Live model|Chat model|Non-OpenAI model/ })).not.toBeInTheDocument();
  fireEvent.change(model, { target: { value: "profile:transcribe" } });
  expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
  vi.mocked(voiceConfigurationApi.save).mockResolvedValue({
    workspace_id: "workspace",
    editable: true,
    revision: 3,
    configuration: {
      mode: "provider", model: "gpt-4o-mini-transcribe", connection_id: "openai",
    },
    effective_model: "gpt-4o-mini-transcribe",
    effective_provider: "openai",
  });
  fireEvent.click(screen.getByRole("button", { name: "Save transcription model" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Voice model saved.", "success"));
  expect(voiceConfigurationApi.save).toHaveBeenCalledWith(
    { connection_id: "openai", revision: 2, mode: "provider", model: "gpt-4o-mini-transcribe" },
    { headers: { "X-Integral-Scope": "ws:workspace" } },
  );
  expect(screen.getByText("Active: gpt-4o-mini-transcribe")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Voice input preferences" }));
  expect(navigate).toHaveBeenCalledWith("voice-input");
});

it("retains a legacy transcription assignment and cancels inline edits without saving", async () => {
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
    workspace_id: "workspace", editable: true, revision: 4,
    configuration: { mode: "provider", model: "whisper-1", connection_id: "openai" },
    effective_model: "whisper-1", effective_provider: "openai",
  });
  show(<VoiceConfiguration connections={connections} profiles={profiles} />);
  const model = await screen.findByRole("combobox", { name: "Transcription model" });
  expect(model).toHaveValue("legacy");
  expect(screen.getByRole("option", { name: "whisper-1 (current assignment)" })).toBeInTheDocument();
  fireEvent.change(model, { target: { value: "disabled" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(model).toHaveValue("legacy");
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
});

it("assigns a registered realtime model inline and omits other model classes", async () => {
  show(<RealtimeVoiceConfiguration connections={connections} profiles={profiles} />);
  const model = await screen.findByRole("combobox", { name: "Realtime model" });
  expect(model).toHaveValue("disabled");
  expect(screen.queryByRole("option", { name: /STT model|Chat model/ })).not.toBeInTheDocument();
  fireEvent.change(model, { target: { value: "profile:live" } });
  expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
  vi.mocked(realtimeVoiceConfigurationApi.save).mockResolvedValue({
    editable: true, revision: 7, effective_model: "gpt-realtime-2.1-mini",
    configuration: { mode: "provider", model: "gpt-realtime-2.1-mini", connection_id: "openai" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save realtime model" }));
  expect(await screen.findByText("Active: gpt-realtime-2.1-mini")).toBeInTheDocument();
  expect(realtimeVoiceConfigurationApi.save).toHaveBeenCalledWith(
    { connection_id: "openai", revision: 6, mode: "provider", model: "gpt-realtime-2.1-mini" },
    { headers: { "X-Integral-Scope": "ws:workspace" } },
  );
});

it("keeps assigned realtime model visible to a non-editor", async () => {
  vi.mocked(realtimeVoiceConfigurationApi.get).mockResolvedValue({
    editable: false, revision: 6,
    configuration: { mode: "provider" }, effective_model: "gpt-realtime-mini",
  });
  show(<RealtimeVoiceConfiguration connections={[]} profiles={profiles} />);
  expect(await screen.findByText("Active: gpt-realtime-mini")).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Realtime model" })).not.toBeInTheDocument();
});
