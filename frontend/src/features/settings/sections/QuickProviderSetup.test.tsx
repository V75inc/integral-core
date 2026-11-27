import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { aiConfigurationApi, realtimeVoiceConfigurationApi, voiceConfigurationApi, type AIConfiguration } from "../../../api/aiConfiguration";
import { QuickProviderSetup } from "./QuickProviderSetup";

const toast = vi.hoisted(() => vi.fn());
vi.mock("../../../context/ToastContext", () => ({ useToast: () => ({ showToast: toast }) }));
vi.mock("../../../api/aiConfiguration", async original => ({
  ...await original<typeof import("../../../api/aiConfiguration")>(),
  aiConfigurationApi: { get: vi.fn(), saveConnection: vi.fn(), saveProfile: vi.fn(), saveRouting: vi.fn() },
  realtimeVoiceConfigurationApi: { get: vi.fn(), save: vi.fn() },
  voiceConfigurationApi: { get: vi.fn(), save: vi.fn() },
}));
const request = { headers: { "X-Integral-Scope": "ws:workspace" } };
const blankSpeech = () => ({
  workspace_id: "workspace", editable: true, revision: 0,
  configuration: { mode: "inherit" as const, connection_id: "", model: "" },
  effective_model: "", effective_provider: "",
});
const blankRealtime = () => ({
  editable: true, revision: 0, configuration: { mode: "disabled" as const }, effective_model: "",
});
function blank(): AIConfiguration {
  return { connections: [], profiles: [], default_profile_id: "", key_policy: "hybrid",
    routing: { mode: "single", heavy_profile_id: "", light_profile_id: "", revision: "rev" },
    effective: { workspace_id: "workspace", source: "unavailable", editable: true, provider: "", model: "", generation_settings: {} },
    harness_limits: { request_limit: 20, token_limit: 1000, run_seconds: 900, request_timeout: 180, idle_timeout: 30 },
  };
}
function show(data = blank()) {
  const busy = vi.fn();
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const drawable = (config: AIConfiguration) => <QueryClientProvider client={client}>
    <QuickProviderSetup data={config} disabled={false} onBusyChange={busy} />
  </QueryClientProvider>;
  const view = render(drawable(data));
  return { ...view, busy, refresh: (config: AIConfiguration) => view.rerender(drawable(config)) };
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(blank());
  vi.mocked(aiConfigurationApi.saveConnection).mockImplementation(async body => ({ ...body, id: body.id || "saved", key_fingerprint: "masked", validated_at: "now", updated_at: "now" }));
  vi.mocked(aiConfigurationApi.saveProfile).mockImplementation(async body => ({ ...body, id: body.model.includes("transcribe") || body.model.includes("realtime") ? body.model : "recommended", updated_at: "now" }));
  vi.mocked(aiConfigurationApi.saveRouting).mockResolvedValue({});
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue(blankSpeech());
  vi.mocked(realtimeVoiceConfigurationApi.get).mockResolvedValue(blankRealtime());
});
it.each([
  ["openai", "gpt-6.1-sol"], ["anthropic", "claude-opus-5-5"],
  ["openrouter", "openai/gpt-6.1-sol"], ["ollama", "deepseek-v4.1-flash"],
])("connects %s with its default model using only provider and key", async (provider, model) => {
  const {busy} = show();
  fireEvent.change(screen.getByRole("combobox", {name:"AI provider"}), {target:{value:provider}});
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  expect(screen.getAllByRole("combobox")).toHaveLength(1);
  expect(screen.queryByLabelText("Reasoning effort")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(aiConfigurationApi.saveRouting).toHaveBeenCalledWith({mode:"single", heavy_profile_id:"recommended", light_profile_id:"", revision:"rev"}, request));
  expect(aiConfigurationApi.saveProfile).toHaveBeenCalledWith(expect.objectContaining({model,generation_settings:{}}), request);
  expect(aiConfigurationApi.saveConnection).toHaveBeenCalledWith(expect.objectContaining({provider,api_key:"synthetic-test-key"}), request);
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Provider saved. Model availability depends on your provider account.","success"));
  if (provider === "openai") {
    for (const model of ["gpt-4o-mini-transcribe", "gpt-realtime-2.1-mini"]) {
      expect(aiConfigurationApi.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ connection_id: "saved", model }), request);
    }
    expect(voiceConfigurationApi.save).toHaveBeenCalledWith({mode:"provider", connection_id:"saved", model:"gpt-4o-mini-transcribe", revision:0}, request);
    expect(realtimeVoiceConfigurationApi.save).toHaveBeenCalledWith({mode:"provider", connection_id:"saved", model:"gpt-realtime-2.1-mini", revision:0}, request);
  } else {
    expect(voiceConfigurationApi.get).not.toHaveBeenCalled();
    expect(realtimeVoiceConfigurationApi.get).not.toHaveBeenCalled();
    expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
    expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
  }
  expect(screen.getByLabelText("API key")).toHaveValue("");
  await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
});
it("clears a key before switching to a different provider", () => {
  show();
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-for-openai"}});
  fireEvent.change(screen.getByRole("combobox", {name:"AI provider"}), {target:{value:"ollama"}});
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(screen.getByRole("button", {name:"Connect provider"})).toBeDisabled();
});
it("does not activate a model when provider validation fails", async () => {
  vi.mocked(aiConfigurationApi.saveConnection).mockRejectedValue(new Error("invalid key"));
  show();
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-invalid"}});
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String),"error"));
  expect(aiConfigurationApi.saveProfile).not.toHaveBeenCalled();
  expect(aiConfigurationApi.saveRouting).not.toHaveBeenCalled();
  expect(screen.queryByText("Your AI is connected.")).not.toBeInTheDocument();
});
it("keeps tuned profiles and dual routing when updating the current provider key", async () => {
  const data = blank();
  data.connections = [{id:"existing",provider:"openai",name:"My account",base_url:"https://public-provider.example/v1",key_fingerprint:"masked",validated_at:"",updated_at:""}];
  data.profiles = [{id:"tuned",connection_id:"existing",name:"My model",model:"gpt-5",generation_settings:{reasoning_effort:"high"},updated_at:""}];
  data.default_profile_id = "tuned";
  data.routing = {mode:"light_heavy",heavy_profile_id:"tuned",light_profile_id:"light",revision:"advanced-revision"};
  data.effective = {...data.effective,source:"workspace_owner",provider:"openai",model:"gpt-5"};
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(data);
  show(data);
  fireEvent.click(screen.getByRole("button", {name:"Change provider"}));
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-replacement"}});
  fireEvent.click(screen.getByRole("button", {name:"Save provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Provider saved. Model availability depends on your provider account.","success"));
  expect(aiConfigurationApi.saveConnection).toHaveBeenCalledWith(expect.objectContaining({id:"existing",api_key:"synthetic-replacement",base_url:"https://public-provider.example/v1"}), request);
  expect(aiConfigurationApi.saveProfile).not.toHaveBeenCalledWith(expect.objectContaining({ model: "gpt-6.1-sol" }), request);
  expect(aiConfigurationApi.saveRouting).not.toHaveBeenCalled();
});
it.each(["platform_only", "member"])("respects %s configuration authority", policy => {
  const data = blank();
  if (policy === "member") data.effective.editable = false; else data.key_policy = policy;
  show(data);
  expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"Connect provider"})).not.toBeInTheDocument();
});
it("reuses partial saves on retry without reporting a failed activation as ready", async () => {
  const data = blank();
  show(data);
  vi.mocked(aiConfigurationApi.saveRouting).mockRejectedValueOnce(new Error("stale revision"));
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String),"error"));
  expect(toast).not.toHaveBeenCalledWith("Provider saved. Model availability depends on your provider account.","success");
  data.connections = [{id:"saved",provider:"openai",name:"OpenAI",base_url:"",key_fingerprint:"masked",validated_at:"",updated_at:""}];
  data.profiles = [{id:"recommended",connection_id:"saved",name:"GPT-5.4 mini",model:"gpt-6.1-sol",generation_settings:{},updated_at:""}];
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(data);
  // The parent query refreshes partial saves even after a failed activation.
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  await waitFor(() => expect(screen.getByRole("button",{name:"Connect provider"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button",{name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Provider saved. Model availability depends on your provider account.","success"));
  expect(aiConfigurationApi.saveConnection).toHaveBeenLastCalledWith(expect.objectContaining({id:"saved"}), request);
  expect(vi.mocked(aiConfigurationApi.saveProfile).mock.calls.filter(([write]) => write.model === "gpt-6.1-sol")).toHaveLength(1);
});

it("offers a personal provider override when the installation already supplies AI", () => {
  const data = blank();
  data.effective = {...data.effective, source:"deployment", model:"openai/gpt-4.1"};
  show(data);
  expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Use my own provider"}));
  expect(screen.getByLabelText("API key")).toBeVisible();
  expect(screen.getByText(/GPT-6\.1 Sol.*will be selected automatically\./)).toBeVisible();
  expect(aiConfigurationApi.saveRouting).not.toHaveBeenCalled();
});

it("does not warn about an absent model during fresh provider setup", () => {
  const data = blank();
  data.effective.harness_policy = {tier:"unverified",recommended:false,evidence:"",message:"Advanced model choice: harness suitability is unverified."};
  show(data);
  expect(screen.getByText(/GPT-6\.1 Sol.*will be selected automatically\./)).toBeVisible();
  expect(screen.queryByText(/harness suitability is unverified/)).not.toBeInTheDocument();
});
it("retains suitability warnings for a connected unverified model", () => {
  const data = blank();
  data.effective = {...data.effective,source:"workspace_owner",provider:"openai",model:"custom-model",harness_policy:{tier:"unverified",recommended:false,evidence:"",message:"Advanced model choice: harness suitability is unverified."}};
  show(data);
  expect(screen.getByText(/harness suitability is unverified/)).toBeVisible();
});

it("keeps explicit disabled realtime and manually chosen transcription settings", async () => {
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
    ...blankSpeech(), revision: 2,
    configuration: {mode:"provider", connection_id:"other-provider", model:"gpt-4o-transcribe"},
  });
  vi.mocked(realtimeVoiceConfigurationApi.get).mockResolvedValue({...blankRealtime(), revision: 1});
  show();
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "success"));
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
  expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
});

it("does not write to a different workspace if the scoped read is inconsistent", async () => {
  const data = blank();
  vi.mocked(aiConfigurationApi.get).mockResolvedValue({...data, effective:{...data.effective, workspace_id:"different"}});
  show();
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "error"));
  expect(aiConfigurationApi.saveConnection).not.toHaveBeenCalled();
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
  expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
});

it("resumes after a realtime save failure without rewriting a completed chat or transcription role", async () => {
  const data = blank();
  const view = show(data);
  vi.mocked(realtimeVoiceConfigurationApi.save).mockRejectedValueOnce(new Error("stale revision"));
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-test-key"}});
  fireEvent.click(screen.getByRole("button", {name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "error"));
  expect(toast).not.toHaveBeenCalledWith(expect.any(String), "success");

  data.connections = [{id:"saved",provider:"openai",name:"OpenAI",base_url:"",key_fingerprint:"masked",validated_at:"",updated_at:""}];
  data.profiles = [{id:"recommended",connection_id:"saved",name:"GPT 6.1 Sol",model:"gpt-6.1-sol",generation_settings:{},updated_at:""}];
  for (const model of ["gpt-4o-mini-transcribe", "gpt-realtime-2.1-mini"]) {
    data.profiles.push({id:model,connection_id:"saved",name:model,model,generation_settings:{},updated_at:""});
  }
  data.default_profile_id = "recommended";
  data.effective = {...data.effective, source:"workspace_owner", provider:"openai", model:"gpt-6.1-sol"};
  // Model/connection reload succeeds, but the inline quick-setup edit must be retryable.
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(data);
  vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
    ...blankSpeech(), revision: 1,
    configuration: {mode:"provider", connection_id:"saved", model:"gpt-4o-mini-transcribe"},
  });
  await waitFor(() => expect(view.busy).toHaveBeenLastCalledWith(false));
  view.refresh(data);
  expect(screen.getByLabelText("API key")).toHaveValue("");
  await waitFor(() => expect(screen.getByRole("button",{name:"Save provider"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button",{name:"Save provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "success"));
  expect(aiConfigurationApi.saveConnection).toHaveBeenLastCalledWith(expect.not.objectContaining({api_key:expect.anything()}), request);
  expect(aiConfigurationApi.saveRouting).toHaveBeenCalledTimes(1);
  expect(voiceConfigurationApi.save).toHaveBeenCalledTimes(1);
  expect(realtimeVoiceConfigurationApi.save).toHaveBeenCalledTimes(2);
  expect(aiConfigurationApi.saveProfile).toHaveBeenCalledTimes(3);
});


it.each(["gpt-live-transcribe", "gpt-transcribe"])(
  "preserves inherited transcription (%s) on an upgraded workspace with no explicit voice revision",
  async effective_model => {
    vi.mocked(voiceConfigurationApi.get).mockResolvedValue({
      ...blankSpeech(),
      effective_model,
      effective_provider: "openai",
    });
    show();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "synthetic-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect provider" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "success"));
    expect(aiConfigurationApi.saveProfile).toHaveBeenCalledWith(
      expect.objectContaining({ connection_id: "saved", model: "gpt-4o-mini-transcribe" }), request,
    );
    expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
    expect(realtimeVoiceConfigurationApi.save).toHaveBeenCalled();
  },
);

it("registers Google's shared light/vision profile once plus a consent-ready embedding profile", async () => {
  const data = blank();
  data.model_policy = { version:"fixture", note:"", providers:{}, specialists:{gemini:{
    light:"gemini-3.1-flash-lite", vision:"gemini-3.1-flash-lite", embedding:"gemini-embedding-001"
  }}};
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(data);
  show(data);
  fireEvent.change(screen.getByLabelText("AI provider"), {target:{value:"gemini"}});
  expect(screen.getByRole("link", {name:/Google AI Studio/})).toHaveAttribute("href","https://aistudio.google.com/apikey");
  fireEvent.change(screen.getByLabelText("API key"), {target:{value:"synthetic-google-key"}});
  fireEvent.click(screen.getByRole("button",{name:"Connect provider"}));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String),"success"));
  expect(vi.mocked(aiConfigurationApi.saveProfile).mock.calls.map(([p])=>p.model)).toEqual(["gemini-3.6-flash","gemini-3.1-flash-lite","gemini-embedding-001"]);
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
  expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
});

it("registers applicable profiles before changing assignments and can retry incomplete registration", async () => {
  const data = blank();
  data.model_policy = { version: "fixture", note: "", providers: {}, specialists: {
    openai: { vision: "gpt-6.1-sol", embedding: "text-embedding-3-small" },
  }};
  vi.mocked(aiConfigurationApi.get).mockResolvedValue(data);
  vi.mocked(aiConfigurationApi.saveProfile).mockImplementation(async body => {
    if (body.model === "text-embedding-3-small") throw new Error("Registration failed");
    return { ...body, id: body.model, updated_at: "now" };
  });
  show(data);
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "fixture" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect provider" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "error"));
  expect(vi.mocked(aiConfigurationApi.saveProfile).mock.calls.map(([p]) => p.model)).toEqual(["gpt-6.1-sol", "text-embedding-3-small"]);
  expect(aiConfigurationApi.saveRouting).not.toHaveBeenCalled();
  expect(voiceConfigurationApi.save).not.toHaveBeenCalled();
  expect(realtimeVoiceConfigurationApi.save).not.toHaveBeenCalled();
});

it("summarizes serving assignments separately from a provider edit or a pending embedding model", () => {
  const data = blank();
  data.effective = { ...data.effective, model: "chat-heavy", provider: "openai", source: "workspace_owner",
    routing: { mode: "light_heavy", heavy_model: "chat-heavy", light_model: "chat-light" } };
  const assignments = {
    realtime: blankRealtime(),
    speech: { ...blankSpeech(), effective_model: "saved-transcription" },
    embeddings: { editable: true, workspace_id: "workspace", revision: 2, provider_overrides_supported: true,
      configuration: { mode: "provider" as const, model: "pending-encoder", connection_id: "saved", dimensions: 768, allow_workspace_text: true },
      profile_id: "serving", effective_provider: "local", effective_model: "serving-encoder", dimensions: 384, pending_model: "pending-encoder", rebuild_status: "pending", error: "" },
    images: { available: true, editable: false, revision: 1, workspace_id: "workspace", configuration: { enabled: true } },
  };
  render(<QueryClientProvider client={new QueryClient()}>
    <QuickProviderSetup data={data} assignments={assignments} disabled={false} onBusyChange={vi.fn()} />
  </QueryClientProvider>);
  const summary = screen.getByRole("group", { name: "Current model assignments" });
  for (const value of ["chat-heavy", "chat-light", "saved-transcription", "serving-encoder", "Workspace assignment", "Off"]) {
    expect(summary).toHaveTextContent(value);
  }
  expect(summary).not.toHaveTextContent("pending-encoder");
  const changeProvider = screen.getByRole("button", { name: "Change provider" });
  expect(changeProvider.closest("header")).toContainElement(screen.getByRole("heading", { name: "Quick Configuration" }));
  fireEvent.click(changeProvider);
  fireEvent.change(screen.getByLabelText("AI provider"), { target: { value: "gemini" } });
  const cancel = screen.getByRole("button", { name: "Cancel" });
  expect(cancel.nextElementSibling).toBe(screen.getByRole("button", { name: "Save provider" }));
  fireEvent.click(cancel);
  expect(screen.getByRole("group", { name: "Current model assignments" })).toHaveTextContent("chat-heavy");
  expect(aiConfigurationApi.saveConnection).not.toHaveBeenCalled();
});
