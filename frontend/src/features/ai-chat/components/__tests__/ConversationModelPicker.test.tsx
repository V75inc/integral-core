import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationModelPicker } from "../ConversationModelPicker";
import { aiConfigurationApi } from "../../../../api/aiConfiguration";

const state = vi.hoisted(() => ({ running: false, showToast: vi.fn() }));
vi.mock("../../AIChatSurface", () => ({
  useChatActivity: () => ({
    activeThreadId: "thread",
    isRunning: state.running,
    activeProviderId: "integral_native",
  }),
}));
vi.mock("../../../settings/hooks/useAIConfiguration", () => ({
  useAIConfiguration: () => ({
    data: {
      effective: { workspace_id: "workspace", editable: true, model: "default-model" },
      profiles: [
        { id: "workspace-default", name: "Main model", model: "default-model" },
        {
          id: "fixed",
          name: "Deep",
          model: "example",
          generation_settings: { reasoning_effort: "high" },
        },
        { id: "stt", name: "Dictation model", model: "gpt-4o-mini-transcribe" },
        { id: "realtime", name: "Live voice model", model: "gpt-realtime-2.1-mini" },
        { id: "embed", name: "Embedding model", model: "embeddinggemma" },
        { id: "policy", name: "Policy-identified voice", model: "provider/custom", harness_policy: { tier: "voice" } },
      ],
    },
  }),
}));
vi.mock("../../../../api/aiConfiguration", () => ({
  aiConfigurationApi: { conversation: vi.fn() },
}));
vi.mock("../../../../context/ToastContext", () => ({
  useToast: () => ({ showToast: state.showToast }),
}));
function show() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConversationModelPicker />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x:0,y:0,left:0,top:0,right:288,bottom:100,width:288,height:100,toJSON:()=>({}),
  });
  state.running = false;
  vi.mocked(aiConfigurationApi.conversation).mockResolvedValue({
    editable: true,
    profile_id: "",
    routing_mode: "inherit",
  });
});
describe("Conversation model settings", () => {
  it("organizes model choices in a compact panel and applies them immediately", async () => {
    show();
    const trigger = await screen.findByRole("button", { name: "Model for this conversation" });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "Chat model settings" })).toBeInTheDocument();
    const picker = screen.getByRole("combobox", { name: "Use model" });
    expect(picker).toHaveFocus();
    expect(screen.queryByRole("option", { name: /dictation model|live voice model|embedding model|policy-identified voice/i })).not.toBeInTheDocument();
    for (const [value, profile, mode] of [
      ["__heavy", "", "heavy"], ["fixed", "fixed", "profile"], ["", "", "inherit"],
    ] as const) {
      fireEvent.change(picker, { target: { value } });
      await waitFor(() => expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith("thread", profile, mode));
      await waitFor(() => expect(picker).toBeEnabled());
    }
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.keyDown(picker, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it("makes a fixed override visible and resets directly to the workspace default", async () => {
    vi.mocked(aiConfigurationApi.conversation).mockResolvedValueOnce({
      editable: true, profile_id: "fixed", routing_mode: "profile",
    });
    show();
    const trigger = await screen.findByRole("button", { name: "Model for this conversation" });
    expect(trigger.querySelector("svg")).toHaveAttribute("stroke", "var(--brand-accent)");
    expect(trigger).not.toHaveTextContent("Chat override");
    expect(trigger).toHaveAttribute("aria-description", "Conversation override: Deep");
    fireEvent.click(trigger);
    expect(screen.getByText("Conversation override: Deep")).toBeInTheDocument();
    expect(screen.getByText("Overrides workspace default (Main model). Settings changes do not replace this chat’s selection.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use workspace default" }));
    await waitFor(() => expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith("thread", "", "inherit"));
    await waitFor(() => expect(trigger.querySelector("svg")).toHaveAttribute("stroke", "currentColor"));
    expect(trigger).not.toHaveTextContent("Workspace default");
    expect(trigger).toHaveAttribute("aria-description", "Workspace default: Main model");
    expect(screen.getByText("Using workspace default")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use workspace default" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Use model" })).toHaveValue("");
  });
  it("distinguishes a heavy-only override from the workspace default", async () => {
    vi.mocked(aiConfigurationApi.conversation).mockResolvedValueOnce({
      editable: true, profile_id: "", routing_mode: "heavy",
    });
    show();
    const trigger = await screen.findByRole("button", { name: "Model for this conversation" });
    expect(trigger.querySelector("svg")).toHaveAttribute("stroke", "var(--brand-accent)");
    expect(trigger).not.toHaveTextContent("Chat override");
    expect(trigger).toHaveAttribute("aria-description", "Conversation override: Heavy only");
    fireEvent.click(trigger);
    expect(screen.getByText("Conversation override: Heavy only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use workspace default" })).toBeEnabled();
  });
  it("prevents changes while the conversation is running", async () => {
    state.running = true;
    show();
    expect(await screen.findByRole("button", { name: "Model for this conversation" })).toBeDisabled();
    expect(aiConfigurationApi.conversation).toHaveBeenCalledTimes(1);
  });
  it("keeps the current model after a rejected change and shows a toast", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Model for this conversation" }));
    vi.mocked(aiConfigurationApi.conversation).mockRejectedValueOnce(new Error("offline"));
    const picker = screen.getByRole("combobox", { name: "Use model" });
    fireEvent.change(picker, { target: { value: "__heavy" } });
    await waitFor(() => expect(state.showToast).toHaveBeenCalledWith("Could not change the conversation model.", "error"));
    expect(picker).toHaveValue("");
  });
  it("closes on outside click and the close control", async () => {
    show();
    const trigger = await screen.findByRole("button", { name: "Model for this conversation" });
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Close model settings" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

it("keeps reasoning collapsed and applies a conversation-only level or profile default", async () => {
  const choices = { heavy: { model: "gpt-5", efforts: ["low", "high"], default_effort: "medium" } };
  vi.mocked(aiConfigurationApi.conversation).mockImplementation(async (_thread, _profile, _mode, efforts) => ({
    editable: true, profile_id: "", routing_mode: "inherit", reasoning_choices: choices,
    reasoning_efforts: efforts?.heavy ? { heavy: efforts.heavy } : {},
  }));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Model for this conversation" }));
  expect(screen.queryByRole("combobox", { name: "Heavy reasoning" })).not.toBeInTheDocument();
  const section = screen.getByRole("button", { name: /^Reasoning/ });
  expect(section).toHaveAttribute("aria-expanded", "false");
  expect(section).toHaveTextContent("Medium · Default");
  fireEvent.click(section);
  const effort = screen.getByRole("combobox", { name: "Heavy reasoning" });
  fireEvent.change(effort, { target: { value: "high" } });
  await waitFor(() => expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith("thread", undefined, undefined, { heavy: "high" }));
  await waitFor(() => expect(effort).toHaveValue("high"));
  expect(state.showToast).toHaveBeenCalledWith("Reasoning updated for this conversation.", "success");
  expect(section).toHaveTextContent("High");
  fireEvent.change(effort, { target: { value: "" } });
  await waitFor(() => expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith("thread", undefined, undefined, { heavy: null }));
  await waitFor(() => expect(effort).toHaveValue(""));
  fireEvent.click(section);
  expect(screen.queryByRole("combobox", { name: "Heavy reasoning" })).not.toBeInTheDocument();
});

it("does not reveal a tooltip when closing restores trigger focus", async () => {
  show();
  const trigger = await screen.findByRole("button", { name: "Model for this conversation" });
  expect(trigger).not.toHaveAttribute("title");
  fireEvent.click(trigger);
  expect(screen.queryByRole("tooltip", { hidden: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close model settings" }));
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("tooltip", { hidden: true })).not.toBeInTheDocument();
  fireEvent.pointerLeave(trigger.parentElement!);
  fireEvent.pointerEnter(trigger.parentElement!);
  expect(screen.getByRole("tooltip", { hidden: true })).toHaveTextContent("Choose model and reasoning");
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Use model" }), { key: "Escape" });
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("tooltip", { hidden: true })).not.toBeInTheDocument();
});
