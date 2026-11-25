import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { Link, MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { aiConfigurationApi } from "../../../api/aiConfiguration";
import { AiConfigurationSection } from "./AiConfigurationSection";
import { RetrievalConfigSection } from "./RetrievalConfigSection";

vi.mock("../../../api/retrievalConfig", () => ({
  retrievalConfigApi: {
    get: vi.fn().mockResolvedValue({
      embedding_model_eager_load: true, retrieve_k_default: 100,
      retrieve_top_n_default: 20, embedding_store_backend: "pgvector",
    }),
  },
}));

it("shows model-specific Max effort without inventing intermediate levels", async () => {
  vi.mocked(aiConfigurationApi.capabilities).mockResolvedValue({
    reasoning_efforts: ["none", "low", "high", "max"],
    max_tokens: true,
    temperature: false,
    top_p: false,
    verbosity: false,
    thinking_budget: false,
    context_window: false,
    verified: true,
  });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  const effort = await screen.findByLabelText("Reasoning effort");
  expect(
    within(effort)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Provider default", "Off", "Low", "High", "Max"]);
});

it("offers a native thinking toggle and retains an explicit off value", async () => {
  vi.mocked(aiConfigurationApi.capabilities).mockResolvedValue({
    reasoning_efforts: [],
    thinking_enabled: true,
    max_tokens: true,
    temperature: true,
    top_p: true,
    verbosity: false,
    thinking_budget: false,
    context_window: true,
    verified: false,
  });
  vi.mocked(aiConfigurationApi.saveProfile).mockResolvedValue({});
  show();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  fireEvent.change(await screen.findByLabelText("Thinking"), {
    target: { value: "false" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await waitFor(() =>
    expect(aiConfigurationApi.saveProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        generation_settings: expect.objectContaining({
          thinking_enabled: false,
        }),
      }),
    ),
  );
});

it("shows thinking budgets directly when effort levels are unavailable", async () => {
  vi.mocked(aiConfigurationApi.capabilities).mockResolvedValue({
    reasoning_efforts: [],
    max_tokens: true,
    temperature: false,
    top_p: false,
    verbosity: false,
    thinking_budget: true,
    context_window: false,
    verified: true,
  });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  expect(await screen.findByLabelText("Thinking budget tokens")).toBeVisible();
  expect(
    screen.queryByText(/does not expose adjustable reasoning/),
  ).not.toBeInTheDocument();
});

vi.mock("./VoiceInputSection", () => ({ VoiceInputSection: () => <p>Voice input preferences</p> }));

const fixture = vi.hoisted(() => ({
  isPending: false,
  isError: false,
  refetch: vi.fn(),
  showToast: vi.fn(),
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
    saveDeploymentLimits: vi.fn(),
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
vi.mock("../../../context/ToastContext", () => ({
  useToast: () => ({ showToast: fixture.showToast }),
}));
function show({
  advanced = true, catalog = true, language = true, initialCategory,
}: {
  advanced?: boolean; catalog?: boolean; language?: boolean;
  initialCategory?: "speech-to-text" | "realtime-voice";
} = {}) {
  const result = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AiConfigurationSection initialCategory={initialCategory} />
    </QueryClientProvider>,
  );
  const advancedToggle = screen.queryByRole("button", { name: "Advanced Configuration" });
  if (advanced && advancedToggle?.getAttribute("aria-expanded") === "false") fireEvent.click(advancedToggle);
  const catalogToggle = screen.queryByRole("button", { name: "Providers & Models" });
  if (catalog && catalogToggle?.getAttribute("aria-expanded") === "false") fireEvent.click(catalogToggle);
  const languageToggle = screen.queryByRole("button", { name: "Language & multimodal" });
  if (language && languageToggle?.getAttribute("aria-expanded") === "false") fireEvent.click(languageToggle);
  return result;
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
    await waitFor(() =>
      expect(fixture.showToast).toHaveBeenCalledWith(
        "Provider connection verified.",
        "success",
      ),
    );
    expect(
      screen.queryByText("Provider connection verified."),
    ).not.toBeInTheDocument();
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
    await waitFor(() =>
      expect(fixture.showToast).toHaveBeenCalledWith(
        "Default profile updated. Changes apply to the next turn.",
        "success",
      ),
    );
    expect(
      screen.queryByText(
        "Default profile updated. Changes apply to the next turn.",
      ),
    ).not.toBeInTheDocument();
  });
  it("explains workspace-owner configuration and keeps connection editing separate", async () => {
    fixture.data.effective.editable = false;
    show();
    expect(screen.getByText("Managed by the workspace owner.")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: /Providers/ }));
    expect(screen.getByRole("button", { name: "Edit provider" })).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

it("toasts failed saves while retaining the model drawer and draft", async () => {
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
  await waitFor(() =>
    expect(fixture.showToast).toHaveBeenCalledWith(
      "Could not save this configuration.",
      "error",
    ),
  );
  expect(
    within(screen.getByRole("dialog")).queryByRole("alert"),
  ).not.toBeInTheDocument();
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
  expect(screen.getByText(/Server default/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Add model" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a model" });
  expect(
    within(dialog).getByRole("textbox", { name: "Profile name" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Model settings" }),
  ).not.toBeInTheDocument();
});

it("reports provider failures through a toast without a page banner", async () => {
  vi.mocked(aiConfigurationApi.testConnection).mockResolvedValue({
    valid: false,
    message: "Provider unavailable",
  });
  show();
  fireEvent.click(screen.getByRole("tab", { name: /Providers/ }));
  fireEvent.click(screen.getByRole("button", { name: "Test provider" }));
  await waitFor(() =>
    expect(fixture.showToast).toHaveBeenCalledWith(
      "Provider unavailable",
      "error",
    ),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("reports provider transport failures through a toast", async () => {
  vi.mocked(aiConfigurationApi.testConnection).mockRejectedValue(
    new Error("offline"),
  );
  show();
  fireEvent.click(screen.getByRole("tab", { name: /Providers/ }));
  fireEvent.click(screen.getByRole("button", { name: "Test provider" }));
  await waitFor(() =>
    expect(fixture.showToast).toHaveBeenCalledWith(
      "Could not test this provider.",
      "error",
    ),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps model assignments separate from the providers and models catalog", () => {
  show({ language: false });
  const catalog = screen.getByRole("region", { name: "Providers & Models" });
  const assignments = screen.getByRole("region", { name: "Model assignments" });
  expect(within(catalog).getByRole("button", { name: "Add model" })).toBeVisible();
  expect(within(catalog).queryByRole("button", { name: "Language & multimodal" })).not.toBeInTheDocument();
  expect(within(assignments).getByRole("button", { name: "Embeddings" })).toBeVisible();
  fireEvent.click(within(catalog).getByRole("tab", { name: /Providers/ }));
  expect(within(catalog).getByRole("button", { name: "Connect provider" })).toBeVisible();
  expect(within(assignments).getByRole("button", { name: "Speech to Text" })).toBeVisible();
});

it("starts categories collapsed and opens one at a time", () => {
  show({ language: false });
  const language = screen.getByRole("button", { name: "Language & multimodal" });
  const embeddings = screen.getByRole("button", { name: "Embeddings" });
  const voice = screen.getByRole("button", { name: "Speech to Text" });
  expect(language).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("tab", { name: "Models 1" })).toBeVisible();
  fireEvent.click(language);
  expect(language).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText("Main model")).toHaveValue("profile");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(embeddings);
  expect(embeddings).toHaveAttribute("aria-expanded", "true");
  expect(language).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("region", { name: "Embedding model assignment" })).toBeVisible();
  fireEvent.click(voice);
  expect(embeddings).toHaveAttribute("aria-expanded", "false");
  expect(voice).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Voice input preferences")).toBeVisible();
  fireEvent.click(voice);
  expect(voice).toHaveAttribute("aria-expanded", "false");
});


it("keeps Quick Configuration visible while both advanced controls start closed independently", () => {
  show({ advanced: false, catalog: false, language: false });
  const advanced = screen.getByRole("button", { name: "Advanced Configuration" });
  expect(screen.getByRole("heading", { name: "Quick Configuration" })).toBeVisible();
  expect(advanced).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("button", { name: "Providers & Models" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Model assignments" })).not.toBeInTheDocument();

  fireEvent.click(advanced);
  const catalog = screen.getByRole("button", { name: "Providers & Models" });
  const assignments = screen.getByRole("region", { name: "Model assignments" });
  expect(advanced).toHaveAttribute("aria-expanded", "true");
  expect(catalog).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("tablist", { name: "AI configuration" })).not.toBeInTheDocument();
  expect(catalog.compareDocumentPosition(assignments) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(assignments).getByRole("button", { name: "Speech to Text" })).toHaveAttribute("aria-expanded", "false");

  fireEvent.click(catalog);
  expect(screen.getByRole("tablist", { name: "AI configuration" })).toBeVisible();
  fireEvent.click(catalog);
  expect(screen.queryByRole("tablist", { name: "AI configuration" })).not.toBeInTheDocument();
  expect(assignments).toBeVisible();
  expect(screen.getByRole("heading", { name: "Quick Configuration" })).toBeVisible();
});

it("opens only the relevant assignment when following a legacy settings link", () => {
  show({ advanced: false, catalog: false, language: false, initialCategory: "speech-to-text" });
  expect(screen.getByRole("button", { name: "Advanced Configuration" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: "Providers & Models" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "Speech to Text" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: "Realtime Voice" })).toHaveAttribute("aria-expanded", "false");
});

it("puts Deployment Limits last and opens it independently of Advanced Configuration", () => {
  show({ advanced: false, catalog: false, language: false });
  const advanced = screen.getByRole("button", { name: "Advanced Configuration" });
  const limits = screen.getByRole("button", { name: "Deployment Limits" });
  expect(advanced.compareDocumentPosition(limits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(advanced).toHaveClass("min-h-24", "w-full");
  expect(limits).toHaveClass("min-h-24", "w-full");
  expect(limits).toHaveAttribute("aria-expanded", "false");

  fireEvent.click(limits);
  const content = screen.getByRole("region", { name: "Deployment Limits" });
  expect(limits).toHaveAttribute("aria-expanded", "true");
  expect(advanced).toHaveAttribute("aria-expanded", "false");
  expect(content).toHaveTextContent("Model requests per turn20");
  expect(content).toHaveTextContent("Tool calls per turn32");
  expect(content).toHaveTextContent("Total tokens per turn1,200,000");
  expect(content).toHaveTextContent("Turn timeout (seconds)900");
  expect(within(content).queryByRole("button", { name: "Edit deployment limits" })).toBeNull();
  expect(content).toHaveTextContent("Approval and access checks always apply.");
  fireEvent.click(limits);
  expect(screen.queryByRole("region", { name: "Deployment Limits" })).not.toBeInTheDocument();
});

it("opens Advanced and Embeddings from Search, then scrolls only once mounted on each visit", async () => {
  const scroll = vi.fn(function (this: HTMLElement) {
    expect(this).toHaveAttribute("id", "embedding-models-heading");
    expect(this).toHaveAttribute("aria-expanded", "true");
    expect(this.closest("[hidden]")).toBeNull();
    expect(screen.getByRole("button", { name: "Advanced Configuration" })).toHaveAttribute("aria-expanded", "true");
  });
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  function SettingsRouteFixture() {
    const { hash } = useLocation();
    if (hash === "#retrieval") return <RetrievalConfigSection embedded />;
    return <>
      <Link to="/settings#retrieval">Return to Search</Link>
      <AiConfigurationSection key={hash} initialCategory={hash === "#embedding-models" ? "embeddings" : undefined} />
    </>;
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = () => <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/settings#ai-models"]}><SettingsRouteFixture /></MemoryRouter>
  </QueryClientProvider>;
  try {
    const view = render(page());
    expect(screen.getByRole("button", { name: "Advanced Configuration" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("link", { name: "Return to Search" }));
    const link = await screen.findByRole("link", { name: "Manage embedding model" });
    fixture.isPending = true;
    fireEvent.click(link);
    expect(screen.getByText("Loading your models")).toBeVisible();
    expect(scroll).not.toHaveBeenCalled();
    fixture.isPending = false;
    view.rerender(page());
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Embeddings" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Embeddings" }));
    fireEvent.click(screen.getByRole("button", { name: "Advanced Configuration" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("link", { name: "Return to Search" }));
    fireEvent.click(await screen.findByRole("link", { name: "Manage embedding model" }));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(2));
  } finally {
    if (previous) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", previous);
    else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});

it("lets a platform administrator cancel, bound and explicitly save overrides or defaults", async () => {
  const host = { ...fixture.data.harness_limits, tool_call_limit: 32 };
  const settings = { revision: 3, host, overrides: { request_limit: 5 }, effective: { ...host, request_limit: 5 } };
  vi.mocked(aiConfigurationApi.saveDeploymentLimits).mockResolvedValue(settings);
  Object.assign(fixture.data, { deployment_limit_settings: settings });
  try {
    show({ advanced: false, catalog: false, language: false });
    fireEvent.click(screen.getByRole("button", { name: "Deployment Limits" }));
    const open = () => fireEvent.click(screen.getByRole("button", { name: "Edit deployment limits" }));
    open();
    const input = screen.getByRole("spinbutton", { name: "Model requests per turn" });
    expect(input).toHaveValue(5);
    expect(screen.getByRole("checkbox", { name: /whole-answer protection/i })).not.toBeChecked();
    fireEvent.change(input, { target: { value: "21" } });
    expect(screen.getByRole("button", { name: "Save limits" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(aiConfigurationApi.saveDeploymentLimits).not.toHaveBeenCalled();
    open();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Model requests per turn" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(vi.mocked(aiConfigurationApi.saveDeploymentLimits).mock.calls[0][0]).toEqual({ revision: 3, overrides: { request_limit: 4 } }));
    await screen.findByRole("button", { name: "Edit deployment limits" });
    open();
    fireEvent.click(screen.getByRole("button", { name: "Use host defaults" }));
    expect(aiConfigurationApi.saveDeploymentLimits).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(vi.mocked(aiConfigurationApi.saveDeploymentLimits).mock.calls[1][0]).toEqual({ revision: 3, overrides: {} }));
    await screen.findByRole("button", { name: "Edit deployment limits" });
    open();
    fireEvent.click(screen.getByRole("checkbox", { name: /whole-answer protection/i }));
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(vi.mocked(aiConfigurationApi.saveDeploymentLimits).mock.calls[2][0]).toEqual({ revision: 3, overrides: { request_limit: 5 }, whole_answer_protection: true }));
  } finally {
    Reflect.deleteProperty(fixture.data, "deployment_limit_settings");
  }
});
