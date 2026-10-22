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
      effective: { workspace_id: "workspace", editable: true },
      profiles: [
        {
          id: "fixed",
          name: "Deep",
          model: "example",
          generation_settings: { reasoning_effort: "high" },
        },
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
describe("Conversation model routing", () => {
  it("opens a brain menu and saves heavy-only, fixed and workspace choices", async () => {
    show();
    const trigger = await screen.findByRole("button", {
      name: "Model for this conversation",
    });
    for (const [label, profile, mode] of [
      [/^Heavy only/, "", "heavy"],
      [/^Deep/, "fixed", "profile"],
      [/^Workspace default/, "", "inherit"],
    ] as const) {
      fireEvent.click(trigger);
      fireEvent.click(screen.getByRole("menuitemradio", { name: label }));
      await waitFor(() =>
        expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith(
          "thread",
          profile,
          mode,
        ),
      );
      await waitFor(() => expect(trigger).toBeEnabled());
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    }
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
  it("prevents changes while the conversation is running", async () => {
    state.running = true;
    show();
    expect(
      await screen.findByRole("button", {
        name: "Model for this conversation",
      }),
    ).toBeDisabled();
    expect(aiConfigurationApi.conversation).toHaveBeenCalledTimes(1);
  });
  it("keeps the current model after a rejected change and shows a toast", async () => {
    show();
    const trigger = await screen.findByRole("button", {
      name: "Model for this conversation",
    });
    fireEvent.click(trigger);
    vi.mocked(aiConfigurationApi.conversation).mockRejectedValueOnce(
      new Error("offline"),
    );
    fireEvent.click(screen.getByRole("menuitemradio", { name: /^Heavy only/ }));
    await waitFor(() =>
      expect(state.showToast).toHaveBeenCalledWith(
        "Could not change the conversation model.",
        "error",
      ),
    );
    fireEvent.click(trigger);
    expect(
      screen.getByRole("menuitemradio", { name: /^Workspace default/ }),
    ).toHaveAttribute("aria-checked", "true");
  });
});
