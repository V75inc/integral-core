import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { aiConfigurationApi } from "../../../api/aiConfiguration";
import { AiConfigurationSection } from "./AiConfigurationSection";

const fixture = vi.hoisted(() => ({
  isPending: false,
  isError: false,
  refetch: vi.fn(),
  data: {
    connections: [
      {
        id: "connection",
        name: "Work account",
        provider: "openai",
        base_url: "",
        key_fingerprint: "masked",
        validated_at: "2026-10-22",
        updated_at: "",
      },
    ],
    profiles: [
      {
        id: "profile",
        connection_id: "connection",
        name: "Everyday",
        model: "gpt-5",
        generation_settings: {},
        updated_at: "",
      },
    ],
    default_profile_id: "profile",
    key_policy: "hybrid",
    effective: {
      workspace_id: "workspace",
      source: "workspace_owner",
      editable: true,
      provider: "openai",
      model: "gpt-5",
      generation_settings: {},
    },
    harness_limits: {
      request_limit: 20,
      token_limit: 1200000,
      run_seconds: 900,
      request_timeout: 180,
      idle_timeout: 30,
    },
  },
}));
vi.mock("../hooks/useAIConfiguration", () => ({
  useAIConfiguration: () => ({
    data: fixture.data,
    isPending: fixture.isPending,
    isError: fixture.isError,
    refetch: fixture.refetch,
  }),
}));
vi.mock("../../../api/aiConfiguration", async (original) => ({
  ...(await original<typeof import("../../../api/aiConfiguration")>()),
  aiConfigurationApi: {
    testConnection: vi.fn(),
    saveConnection: vi.fn(),
    saveProfile: vi.fn(),
    capabilities: vi.fn(),
  },
}));
vi.mock("../../../api/modelCredentials", async (original) => ({
  ...(await original<typeof import("../../../api/modelCredentials")>()),
  modelCredentialsApi: { get: vi.fn().mockResolvedValue(null) },
}));
function show() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AiConfigurationSection />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  fixture.isPending = false;
  fixture.isError = false;
  fixture.data.effective.editable = true;
  fixture.data.effective.source = "workspace_owner";
  vi.mocked(aiConfigurationApi.capabilities).mockResolvedValue({
    reasoning_efforts: ["low", "medium", "high"],
    max_tokens: true,
    temperature: false,
    top_p: false,
    verbosity: false,
    thinking_budget: false,
    context_window: false,
    verified: true,
  });
});

describe("AI connection and model configuration", () => {
  it("tests the saved connection without requesting its key", async () => {
    vi.mocked(aiConfigurationApi.testConnection).mockResolvedValue({
      valid: true,
      message: "verified",
    });
    show();
    fireEvent.click(screen.getByRole("tab", { name: /Providers/ }));
    fireEvent.click(screen.getByRole("button", { name: "Test provider" }));
    await screen.findByText("Provider verified using the saved key.");
    expect(aiConfigurationApi.testConnection).toHaveBeenCalledWith(
      "connection",
    );
    expect(screen.queryByText("Update models & keys")).not.toBeInTheDocument();
  });
  it("saves supported reasoning and output options without credentials", async () => {
    vi.mocked(aiConfigurationApi.saveProfile).mockResolvedValue({});
    show();
    fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
    const effort = await screen.findByLabelText("Reasoning effort");
    fireEvent.change(effort, { target: { value: "high" } });
    fireEvent.change(screen.getByLabelText("Maximum output tokens"), {
      target: { value: "4096" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() =>
      expect(aiConfigurationApi.saveProfile).toHaveBeenCalled(),
    );
    expect(aiConfigurationApi.saveProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "profile",
        model: "gpt-5",
        generation_settings: { reasoning_effort: "high", max_tokens: 4096 },
      }),
    );
    expect(
      JSON.stringify(vi.mocked(aiConfigurationApi.saveProfile).mock.calls),
    ).not.toContain("api_key");
    expect(screen.queryByLabelText("Temperature")).not.toBeInTheDocument();
  });
  it("explains workspace-owner configuration and keeps connection editing separate", async () => {
    fixture.data.effective.editable = false;
    show();
    expect(
      screen.getByText(
        /Changes to your personal profiles do not change this workspace/,
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: /Providers/ }));
    fireEvent.click(screen.getByRole("button", { name: "Edit provider" }));
    expect(screen.getByLabelText("Provider")).toBeDisabled();
    expect(
      screen.getByLabelText(
        "Replacement API key (leave blank to keep saved key)",
      ),
    ).toHaveValue("");
  });
});

it("keeps failed saves inside the model drawer", async () => {
  vi.mocked(aiConfigurationApi.saveProfile).mockRejectedValue(
    new Error("Unavailable"),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  await screen.findByLabelText("Reasoning effort");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  expect(
    await within(screen.getByRole("dialog")).findByRole("alert"),
  ).toBeVisible();
  expect(screen.getByLabelText("Profile name")).toHaveValue("Everyday");
});
it("allows provider defaults when capability metadata is unavailable", async () => {
  vi.mocked(aiConfigurationApi.capabilities).mockRejectedValue(
    new Error("Unavailable"),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  expect(
    await screen.findByText(/Model capabilities are unavailable/),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
  expect(screen.queryByLabelText("Reasoning effort")).not.toBeInTheDocument();
});
it("shows an actionable error with retry", () => {
  fixture.isError = true;
  show();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(fixture.refetch).toHaveBeenCalledOnce();
});
it("shows loading without editable actions", () => {
  fixture.isPending = true;
  show();
  expect(screen.getByText(/Loading/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Add model" }),
  ).not.toBeInTheDocument();
});

it("offers connecting a provider when the account has no configuration", () => {
  const connections = fixture.data.connections;
  const profiles = fixture.data.profiles;
  fixture.data.connections = [];
  fixture.data.profiles = [];
  try {
    show();
    expect(
      screen.getByRole("button", { name: "Connect provider" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Edit profile" }),
    ).not.toBeInTheDocument();
  } finally {
    fixture.data.connections = connections;
    fixture.data.profiles = profiles;
  }
});
it("supports arrow-key tab navigation", () => {
  show();
  const models = screen.getByRole("tab", { name: /Models/ });
  models.focus();
  fireEvent.keyDown(models, { key: "ArrowRight" });
  const connections = screen.getByRole("tab", { name: /Providers/ });
  expect(connections).toHaveFocus();
  expect(connections).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("button", { name: "Test provider" })).toBeVisible();
});

it("opens a model override editor while the active configuration comes from ENV", async () => {
  fixture.data.effective.source = "deployment";
  show();
  expect(screen.getByText("Managed by your deployment")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Add your own model" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a model" });
  expect(
    within(dialog).getByRole("textbox", { name: "Profile name" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Model settings" }),
  ).not.toBeInTheDocument();
});
