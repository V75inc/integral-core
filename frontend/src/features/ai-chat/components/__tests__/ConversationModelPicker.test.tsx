import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationModelPicker } from "../ConversationModelPicker";
import { aiConfigurationApi } from "../../../../api/aiConfiguration";

const state = vi.hoisted(() => ({ running: false }));
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
  state.running = false;
  vi.mocked(aiConfigurationApi.conversation).mockResolvedValue({
    editable: true,
    profile_id: "",
    routing_mode: "inherit",
  });
});
describe("Conversation model routing", () => {
  it("saves heavy-only, fixed-profile and inherited routing explicitly", async () => {
    show();
    const picker = await screen.findByRole("combobox", {
      name: "Model for this conversation",
    });
    for (const [choice, profile, mode] of [
      ["__heavy", "", "heavy"],
      ["fixed", "fixed", "profile"],
      ["", "", "inherit"],
    ] as const) {
      fireEvent.change(picker, { target: { value: choice } });
      await waitFor(() =>
        expect(aiConfigurationApi.conversation).toHaveBeenLastCalledWith(
          "thread",
          profile,
          mode,
        ),
      );
      await waitFor(() => expect(picker).toBeEnabled());
    }
  });
  it("prevents changes while the conversation is running", async () => {
    state.running = true;
    show();
    expect(
      await screen.findByRole("combobox", {
        name: "Model for this conversation",
      }),
    ).toBeDisabled();
    expect(aiConfigurationApi.conversation).toHaveBeenCalledTimes(1);
  });
});
