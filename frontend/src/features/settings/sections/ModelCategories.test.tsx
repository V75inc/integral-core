import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AttachmentAnalysisConfiguration } from "./AttachmentAnalysisConfiguration";
import { EmbeddingConfiguration } from "./EmbeddingConfiguration";
import { VoiceConfiguration } from "./VoiceConfiguration";
import { RealtimeVoiceConfiguration } from "./RealtimeVoiceConfiguration";
import { ModelRoutingSection } from "./ModelRoutingSection";
import {
  attachmentAnalysisApi,
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
  attachmentAnalysisApi: { get: vi.fn(), save: vi.fn() },
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
  const encoder = { ...profiles[0], model: "text-embedding-3-small", id: "encoder", name: "Search" };
  show(<EmbeddingConfiguration connections={connections} profiles={[encoder]} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Embedding model" }), {
    target: { value: "encoder" },
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
  expect(screen.getByText("text-embedding-3-small · Saved OpenAI")).toBeVisible();
  expect(screen.queryByLabelText("Embedding model ID")).not.toBeInTheDocument();
  expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
});
it("shows an accurate provider restriction for unsupported search stores", async () => {
  vi.mocked(embeddingConfigurationApi.get).mockResolvedValue({
    ...config,
    provider_overrides_supported: false,
  });
  const encoder = { ...profiles[0], model: "text-embedding-3-small", id: "encoder", name: "Search" };
  show(<EmbeddingConfiguration connections={connections} profiles={[encoder]} />);
  expect(await screen.findByRole("option", { name: /Search · Saved OpenAI/ })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Dimensions and consent" })).not.toBeInTheDocument();
  expect(screen.queryByText("Search model")).not.toBeInTheDocument();

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


it("uses the same assignment label, active readout and selector for Main, Speech to Text and Realtime", async () => {
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
    workspace_id: "workspace", editable: true, revision: 2, effective_provider: "openai",
    effective_model: "gpt-4o-mini-transcribe",
    configuration: { mode: "provider", model: "gpt-4o-mini-transcribe", connection_id: "openai" },
  });
  vi.mocked(realtimeVoiceConfigurationApi.get).mockResolvedValue({
    editable: true, revision: 6, effective_model: "gpt-realtime-2.1-mini",
    configuration: { mode: "provider", model: "gpt-realtime-2.1-mini", connection_id: "openai" },
  });
  show(<>
    <ModelRoutingSection inline onEdit={vi.fn()} data={{
      connections, profiles: [
        ...profiles.filter(p => p.id === "chat"),
        { ...profiles[2], id: "second-chat", model: "gpt-6.2-sol" },
      ],
      default_profile_id: "chat", key_policy: "hybrid",
      effective: { workspace_id: "workspace", source: "workspace_owner", editable: true, provider: "openai", model: "gpt-6.1-sol", generation_settings: {} },
      harness_limits: { request_limit: 20, token_limit: 1000, run_seconds: 60, request_timeout: 60, idle_timeout: 30 },
    }} />
    <VoiceConfiguration connections={connections} profiles={profiles} />
    <RealtimeVoiceConfiguration connections={connections} profiles={profiles} />
  </>);
  const main = await screen.findByRole("combobox", { name: "Main model" });
  expect(screen.getByRole("option", { name: "Chat model · Saved OpenAI · gpt-6.1-sol" })).toHaveValue("chat");
  expect(screen.getByRole("option", { name: "Chat model · Saved OpenAI · gpt-6.2-sol" })).toHaveValue("second-chat");
  for (const [label, active, name] of [
    ["Main model", "gpt-6.1-sol", "Chat model"],
    ["Transcription model", "gpt-4o-mini-transcribe", "STT model"],
    ["Realtime model", "gpt-realtime-2.1-mini", "Live model"],
  ]) {
    const select = await screen.findByRole("combobox", { name: label });
    expect(select).toHaveAccessibleDescription(`Active: ${active}`);
    expect(select.parentElement?.className).toBe(main.parentElement?.className);
    expect((select as HTMLSelectElement).selectedOptions[0]).toHaveTextContent(`${name} · Saved OpenAI · ${active}`);
  }
});

const visionConfig = {
  workspace_id: "workspace", available: true, editable: true, can_choose_profile: true,
  recommended_model: "gpt-5.4-mini", configuration: { enabled: true, profile_id: "" }, revision: 4,
};
const visionProfiles = [...profiles, { ...profiles[0], id: "vision", name: "My vision model", model: "gpt-4o", supports_vision: true }];
const visionAssignment = <AttachmentAnalysisConfiguration connections={connections} profiles={visionProfiles} />;
const visionRequest = { headers: { "X-Integral-Scope": "ws:workspace" } };

it("assigns vision inline, filters models, cancels drafts and restores the saved selection", async () => {
  vi.mocked(attachmentAnalysisApi.get).mockResolvedValue(visionConfig);
  const saved = { ...visionConfig, configuration: { enabled: true, profile_id: "vision" }, revision: 5 };
  vi.mocked(attachmentAnalysisApi.save).mockResolvedValue(saved);
  const view = show(visionAssignment);
  const select = await screen.findByRole("combobox", { name: "Vision model" });
  expect(select).toHaveAccessibleDescription("Active: gpt-5.4-mini");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /STT model|Live model|Chat model/ })).not.toBeInTheDocument();
  fireEvent.change(select, { target: { value: "vision" } });
  expect(attachmentAnalysisApi.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(select).toHaveValue("");
  fireEvent.change(select, { target: { value: "vision" } });
  fireEvent.click(screen.getByRole("button", { name: "Save vision model" }));
  await screen.findByText("Active: gpt-4o");
  expect(attachmentAnalysisApi.save).toHaveBeenCalledWith({ enabled: true, profile_id: "vision", revision: 4 }, visionRequest);
  view.unmount();
  vi.mocked(attachmentAnalysisApi.get).mockResolvedValue(saved);
  show(visionAssignment);
  expect(await screen.findByRole("combobox", { name: "Vision model" })).toHaveValue("vision");
});

it("edits processing as a separate preference and retains the assigned model while off", async () => {
  const configured = { ...visionConfig, configuration: { enabled: true, profile_id: "vision" } };
  vi.mocked(attachmentAnalysisApi.get).mockResolvedValue(configured);
  vi.mocked(attachmentAnalysisApi.save).mockResolvedValue({
    ...configured, configuration: { enabled: false, profile_id: "vision" }, revision: 5,
  });
  show(visionAssignment);
  fireEvent.click(await screen.findByRole("button", { name: "Image processing preferences" }));
  expect(screen.getByRole("dialog")).not.toContainElement(screen.queryByRole("combobox", { name: "Vision model" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Automatically analyze image attachments" }));
  fireEvent.click(screen.getByRole("button", { name: "Save preferences" }));
  await screen.findByText("Active: Off");
  expect(attachmentAnalysisApi.save).toHaveBeenCalledWith({ enabled: false, revision: 4 }, visionRequest);
  const select = screen.getByRole("combobox", { name: "Vision model" });
  expect(select).toHaveValue("vision");
  fireEvent.change(select, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save vision model" }));
  await waitFor(() => expect(attachmentAnalysisApi.save).toHaveBeenLastCalledWith({ enabled: false, profile_id: "", revision: 5 }, visionRequest));
});

it("withholds owner controls for a read-only workspace member", async () => {
  vi.mocked(attachmentAnalysisApi.get).mockResolvedValue({
    ...visionConfig, editable: false, can_choose_profile: false, recommended_model: "", configuration: { enabled: true },
  });
  show(visionAssignment);
  expect(await screen.findByText("Active: Workspace assignment")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Image processing preferences" })).not.toBeInTheDocument();
});

it("offers every eligible registered profile and no model from the connection alone", async () => {
  vi.mocked(attachmentAnalysisApi.get).mockResolvedValue({ ...visionConfig, recommended_model: "" });
  const { unmount } = show(<AttachmentAnalysisConfiguration connections={connections} profiles={profiles} />);
  const select = await screen.findByRole("combobox", { name: "Vision model" });
  expect(Array.from((select as HTMLSelectElement).options, option => option.value)).toEqual([""]);
  expect(screen.getByText(/Register a vision-capable model/)).toBeVisible();
  unmount();
  const additional = { ...visionProfiles[visionProfiles.length - 1], id: "second", model: "gpt-6.1-sol", name: "My multimodal model" };
  show(<AttachmentAnalysisConfiguration connections={connections} profiles={[...visionProfiles, additional]} />);
  const updated = await screen.findByRole("combobox", { name: "Vision model" });
  expect(Array.from((updated as HTMLSelectElement).options, option => option.value)).toEqual(["", "vision", "second"]);
  expect(screen.getByRole("option", { name: /My multimodal model/ })).toHaveValue("second");
});

it("preselects the saved encoder for review while showing the serving model and requiring consent", async () => {
  const encoder = { ...profiles[0], id: "encoder", name: "Private encoder", model: "private-v2", embedding_only: true };
  vi.mocked(embeddingConfigurationApi.get).mockResolvedValue({ ...config, revision: 0 });
  show(<EmbeddingConfiguration connections={connections} profiles={[encoder]} recommendedProfile={encoder} embedded />);
  const selector = await screen.findByRole("combobox", { name: "Embedding model" });
  expect(selector).toHaveValue("encoder");
  expect(selector).toHaveAccessibleDescription("Active: all-MiniLM-L6-v2 · 384 dimensions");
  expect(screen.getByRole("option", { name: /awaiting consent/ })).toHaveValue("encoder");
  fireEvent.click(screen.getByRole("button", { name: "Dimensions and consent" }));
  expect(screen.getByText("private-v2 · Saved OpenAI")).toBeVisible();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Save model" })).toBeDisabled();
  expect(embeddingConfigurationApi.test).not.toHaveBeenCalled();
  expect(embeddingConfigurationApi.save).not.toHaveBeenCalled();
});
