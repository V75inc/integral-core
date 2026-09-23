/**
 * Phase 8 Plan 08-02 Task 2 — Vitest coverage for ConnectorsSection.
 *
 * Covers (per plan):
 *   1. renders empty state when list is empty
 *   2. renders one row per connector with derived conflict_policy pill
 *   3. clicking sync button calls connectorsApi.sync(id) and toasts stats
 *   4. clicking edit opens ConnectorEditModal pre-filled
 *   5. clicking delete opens confirm and calls connectorsApi.delete
 *   6. Add Connector opens the vetted catalog library; Back returns to Connected
 *   7. Gmail/QuickBooks wizards are not on home until a connected row is configured
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  within,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../../../../api/connectors", () => ({
  MCP_OAUTH_MESSAGE_TYPE: "integral:mcp-oauth",
  connectorsApi: {
    list: vi.fn(),
    listCatalog: vi.fn(),
    getCatalogEntry: vi.fn(),
    installFromCatalog: vi.fn(),
    create: vi.fn(),
    mountMcp: vi.fn(),
    searchMcpRegistry: vi.fn(),
    previewMcpRegistryMount: vi.fn(),
    mountFromRegistry: vi.fn(),
    refreshMcp: vi.fn(),
    health: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sync: vi.fn(),
    listTools: vi.fn(),
    listBindings: vi.fn(),
    createBinding: vi.fn(),
    deleteBinding: vi.fn(),
  },
}));

vi.mock(
  "../../../../components/settings/connectors/GmailConnectorSetup",
  () => ({
    GmailConnectorSetup: ({
      initialConnectorId,
    }: {
      initialConnectorId?: string;
    }) => (
      <div data-testid="gmail-connector-setup">
        gmail-setup-{initialConnectorId}
      </div>
    ),
  }),
);

vi.mock("../../../../components/settings/QuickBooksConnectorSettings", () => ({
  QuickBooksConnectorSettings: () => (
    <div data-testid="quickbooks-connector-settings">qb-settings</div>
  ),
}));

vi.mock("../../hooks/useAgentiveCapability", () => ({
  useAgentiveCapability: vi.fn(),
}));

vi.mock("../../../../context/ScopeContext", () => ({
  useScopeOptional: vi.fn(),
}));

vi.mock("../../../../context/AuthContext", () => ({
  useAuthOptional: vi.fn(),
}));

import {
  connectorsApi,
  type ConnectorResponse,
} from "../../../../api/connectors";
import { useAgentiveCapability } from "../../hooks/useAgentiveCapability";
import { useScopeOptional } from "../../../../context/ScopeContext";
import { useAuthOptional } from "../../../../context/AuthContext";
import { ConnectorsSection } from "../ConnectorsSection";
import { ToastProvider } from "../../../../context/ToastContext";
import { ConfirmProvider } from "../../../../context/ConfirmContext";

const mockedList = connectorsApi.list as unknown as ReturnType<typeof vi.fn>;
const mockedCatalog = connectorsApi.listCatalog as unknown as ReturnType<
  typeof vi.fn
>;
const mockedSync = connectorsApi.sync as unknown as ReturnType<typeof vi.fn>;
const mockedDelete = connectorsApi.delete as unknown as ReturnType<
  typeof vi.fn
>;
const mockedSearchRegistry =
  connectorsApi.searchMcpRegistry as unknown as ReturnType<typeof vi.fn>;
const mockedCapability = useAgentiveCapability as unknown as ReturnType<
  typeof vi.fn
>;

function makeConnector(
  overrides: Partial<ConnectorResponse> = {},
): ConnectorResponse {
  return {
    id: "con-1",
    kind: "jvagent",
    owner: "u-1",
    auth_state: {},
    sync_cursor: null,
    mapping_profile: null,
    permissions: [],
    capabilities: [],
    conflict_policy: "last_write_wins",
    subclass_slug: "github_issues",
    sync_interval_seconds: 300,
    last_synced_at: null,
    created_at: "2026-05-17T00:00:00Z",
    updated_at: "2026-05-17T00:00:00Z",
    ...overrides,
  };
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ConfirmProvider>
          <ConnectorsSection />
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (useScopeOptional as unknown as ReturnType<typeof vi.fn>).mockReturnValue(
    null,
  );
  (useAuthOptional as unknown as ReturnType<typeof vi.fn>).mockReturnValue(
    null,
  );
  // Default: agentive layer ON
  mockedCapability.mockReturnValue({ enabled: true, isLoading: false });
  mockedSync.mockResolvedValue({
    created: 2,
    updated: 1,
    conflict: 0,
    archived: 0,
    errors: [],
  });
  mockedDelete.mockResolvedValue(undefined);
  mockedSearchRegistry.mockResolvedValue({
    entries: [],
    count: 0,
    next_cursor: null,
  });
  (
    connectorsApi.listBindings as unknown as ReturnType<typeof vi.fn>
  ).mockResolvedValue({
    bindings: [],
    total: 0,
  });
  mockedCatalog.mockResolvedValue({
    entries: [
      {
        slug: "gmail",
        display_name: "Gmail",
        description: "Mirror Gmail labels",
        category: "native",
        kind: "sync",
        icon: "gmail",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
          ],
        },
      },
      {
        slug: "quickbooks",
        display_name: "QuickBooks",
        description: "Mirror QuickBooks Online",
        category: "native",
        kind: "sync",
        icon: "quickbooks",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
            {
              name: "environment",
              label: "Environment (sandbox or production)",
              secret: false,
              required: false,
            },
          ],
        },
      },
      {
        slug: "github_issues",
        display_name: "GitHub Issues",
        description: "Mirror GitHub issues",
        category: "native",
        kind: "sync",
        icon: "github",
        vetted: true,
        auth: {
          type: "env",
          fields: [
            {
              name: "owner",
              label: "Repository owner",
              secret: false,
              required: true,
            },
            {
              name: "repo",
              label: "Repository name",
              secret: false,
              required: true,
            },
          ],
        },
      },
      {
        slug: "calendly",
        display_name: "Calendly",
        description: "Book Calendly events",
        category: "mcp_server",
        kind: "mcp",
        icon: "calendly",
        vetted: true,
        auth: { type: "none", fields: [] },
      },
      {
        slug: "notion",
        display_name: "Notion",
        description: "Read and update Notion pages",
        category: "mcp_server",
        kind: "mcp",
        icon: "notion",
        vetted: true,
        auth: { type: "none", fields: [] },
      },
      {
        slug: "google_drive",
        display_name: "Google Drive",
        description: "Search and edit Google Drive files",
        category: "mcp_server",
        kind: "mcp",
        icon: "google_drive",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
          ],
        },
      },
      {
        slug: "google_gmail",
        display_name: "Gmail MCP",
        description: "Search Gmail threads and create drafts",
        category: "mcp_server",
        kind: "mcp",
        icon: "gmail",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
          ],
        },
      },
      {
        slug: "google_sheets",
        display_name: "Google Sheets",
        description: "Read and update spreadsheets",
        category: "mcp_server",
        kind: "mcp",
        icon: "google_sheets",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
          ],
        },
      },
      {
        slug: "quickbooks_mcp",
        display_name: "QuickBooks MCP",
        description: "Live QuickBooks Online tools",
        category: "mcp_package",
        kind: "mcp",
        icon: "quickbooks_mcp",
        transport: "stdio",
        vetted: true,
        auth: {
          type: "oauth2",
          fields: [
            {
              name: "client_id",
              label: "Client ID",
              secret: false,
              required: false,
            },
            {
              name: "client_secret",
              label: "Client secret",
              secret: true,
              required: false,
            },
            {
              name: "environment",
              label: "Environment (sandbox or production)",
              secret: false,
              required: false,
            },
            {
              name: "QUICKBOOKS_DISABLE_WRITE",
              label: "Disable create tools (true/false)",
              secret: false,
              required: false,
            },
            {
              name: "QUICKBOOKS_DISABLE_UPDATE",
              label: "Disable update tools (true/false)",
              secret: false,
              required: false,
            },
            {
              name: "QUICKBOOKS_DISABLE_DELETE",
              label: "Disable delete tools (true/false)",
              secret: false,
              required: false,
            },
          ],
        },
      },
    ],
    total: 9,
  });
});

afterEach(() => {
  cleanup();
});

describe("<ConnectorsSection />", () => {
  it("renders empty state when list is empty", async () => {
    mockedList.mockResolvedValueOnce({ connectors: [], total: 0 });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/No connectors yet\./i)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("heading", { name: "Connectors" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Connected" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Setup wizards")).not.toBeInTheDocument();
    expect(screen.queryByText("All connectors")).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/Search MCP servers/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("gmail-connector-setup"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("quickbooks-connector-card"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("quickbooks-connector-settings"),
    ).not.toBeInTheDocument();
  });

  it("renders one row per connector without the internal conflict-policy pill", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({ id: "con-1", conflict_policy: "last_write_wins" }),
        makeConnector({
          id: "con-2",
          kind: "mcp",
          subclass_slug: null,
          conflict_policy: null,
        }),
      ],
      total: 2,
    });
    renderPanel();
    // Two rows render — verify by counting the per-row Sync buttons.
    await waitFor(() => {
      expect(screen.getByLabelText("Sync connector con-1")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Sync connector con-2")).toBeInTheDocument();
    // Sync-engine internals stay off the card: no policy keyword pills.
    expect(screen.queryByText("last_write_wins")).toBeNull();
    expect(screen.queryByText("no conflict rule")).toBeNull();
  });

  it("shows MCP connector name and a compact tool inspector", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: "mcp-1",
          kind: "mcp",
          subclass_slug: "mcp",
          conflict_policy: null,
          health_status: "ok",
          auth_state: {
            display_name: "Firecrawl MCP Server",
            transport: "streamable_http",
            url: "https://mcp.firecrawl.dev/mcp",
            registry: {
              name: "io.github.firecrawl/firecrawl-mcp-server",
              version: "3.24.0",
            },
            discovered_tools: [
              { name: "scrape", description: "Scrape a URL" },
              { name: "crawl", description: "Crawl a site" },
            ],
          },
        }),
      ],
      total: 1,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("Firecrawl MCP Server")).toBeInTheDocument();
    });
    expect(
      screen.getByText("io.github.firecrawl/firecrawl-mcp-server"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "View tools for Firecrawl MCP Server",
      }),
    ).toHaveTextContent("View 2 tools");
    expect(screen.queryByText("scrape")).not.toBeInTheDocument();
    expect(screen.queryByText("crawl")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "View tools for Firecrawl MCP Server",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Firecrawl MCP Server tools",
    });
    expect(within(dialog).getByText("scrape")).toBeInTheDocument();
    expect(within(dialog).getByText("crawl")).toBeInTheDocument();
    expect(within(dialog).getByText("Scrape a URL")).toBeInTheDocument();
    expect(screen.queryByText("MCP mount")).not.toBeInTheDocument();
  });

  it("Add Connector opens the vetted catalog library and Back returns home", async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Connected" }),
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Connector" }));
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Connector library" }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByPlaceholderText(/Search vetted connectors/i),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Gmail", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("QuickBooks", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("GitHub Issues", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Calendly", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Notion", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Google Drive", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Gmail MCP", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Google Sheets", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("QuickBooks MCP", { selector: ":not(title)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Connected" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/Search MCP servers/i),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to connectors" }));
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Connected" }),
      ).toBeInTheDocument();
    });
  });

  it("QuickBooks install sheet lets the operator enter Client ID and secret", async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Connector" }),
    );
    const qbName = await screen.findByText("QuickBooks", {
      selector: ":not(title)",
    });
    const card = qbName.closest("li");
    expect(card).not.toBeNull();
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Add" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Install QuickBooks" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Client ID")).toBeInTheDocument();
    expect(screen.getByText("Client secret")).toBeInTheDocument();
    expect(
      screen.getByText(/Leave a field blank to use the matching server/i),
    ).toBeInTheDocument();
  });

  it("QuickBooks MCP install sheet starts Intuit OAuth, not a token paste", async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Connector" }),
    );
    const qbName = await screen.findByText("QuickBooks MCP", {
      selector: ":not(title)",
    });
    const card = qbName.closest("li");
    expect(card).not.toBeNull();
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Add" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Install QuickBooks MCP" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Client ID")).toBeInTheDocument();
    expect(screen.getByText("Client secret")).toBeInTheDocument();
    expect(screen.queryByText("Refresh token")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Connect opens Intuit sign-in/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Disable create tools (true/false)"),
    ).not.toBeInTheDocument();
    const disableCreate = screen.getByRole("button", {
      name: "Disable create tools",
    });
    const disableUpdate = screen.getByRole("button", {
      name: "Disable update tools",
    });
    const disableDelete = screen.getByRole("button", {
      name: "Disable delete tools",
    });
    expect(disableCreate).toHaveAttribute("aria-pressed", "false");
    expect(disableUpdate).toHaveAttribute("aria-pressed", "false");
    expect(disableDelete).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(disableCreate);
    expect(disableCreate).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("Google Drive install sheet starts OAuth, not a silent MCP mount", async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Connector" }),
    );
    const driveName = await screen.findByText("Google Drive", {
      selector: ":not(title)",
    });
    const card = driveName.closest("li");
    expect(card).not.toBeNull();
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Add" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Install Google Drive" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Client ID")).toBeInTheDocument();
    expect(screen.getByText("Client secret")).toBeInTheDocument();
    expect(
      screen.getByText(/Connect opens a sign-in popup/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("Gmail settings live on the connected row, not on home until opened", async () => {
    mockedList.mockResolvedValue({
      connectors: [
        makeConnector({ id: "g-1", subclass_slug: "gmail", kind: "jvagent" }),
      ],
      total: 1,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByLabelText("Sync connector g-1")).toBeInTheDocument();
    });
    expect(screen.getByText("Gmail")).toBeInTheDocument();
    expect(
      screen.queryByTestId("gmail-connector-setup"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Configure connector g-1"));
    expect(screen.getByTestId("gmail-connector-setup")).toBeInTheDocument();
  });

  it("clicking sync calls connectorsApi.sync(id) and toasts stats", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [makeConnector({ id: "con-1" })],
      total: 1,
    });
    renderPanel();
    const btn = await screen.findByLabelText("Sync connector con-1");
    fireEvent.click(btn);
    await waitFor(() => {
      expect(mockedSync).toHaveBeenCalledWith("con-1");
    });
    // Toast contains the {created, updated, conflict} stats line.
    await waitFor(() => {
      expect(
        screen.getByText(/2 created, 1 updated, 0 conflicts/i),
      ).toBeInTheDocument();
    });
  });

  it("clicking edit opens ConnectorEditModal pre-filled", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({ id: "con-1", subclass_slug: "github_issues" }),
      ],
      total: 1,
    });
    renderPanel();
    const editBtn = await screen.findByLabelText("Edit connector con-1");
    fireEvent.click(editBtn);
    // Modal title appears
    await waitFor(() => {
      expect(
        screen.getByText(/Edit connector — jvagent:con-1/i),
      ).toBeInTheDocument();
    });
  });

  it("clicking delete opens confirm and calls connectorsApi.delete on confirm", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [makeConnector({ id: "con-1" })],
      total: 1,
    });
    renderPanel();
    const delBtn = await screen.findByLabelText("Delete connector con-1");
    fireEvent.click(delBtn);
    const confirmBtn = await screen.findByRole("button", { name: /^Delete$/ });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith("con-1");
    });
  });

  it("scope filter narrows the connected list to shared rows", async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: "con-shared",
          connection_mode: "shared",
          label: "Team Drive",
          subclass_slug: "drive_native",
        }),
        makeConnector({
          id: "con-mine",
          connection_mode: "per_user",
          label: "My Drive",
          subclass_slug: "drive_native",
        }),
      ],
      total: 2,
    });
    renderPanel();
    await screen.findByText("Team Drive");
    await screen.findByText("My Drive");
    fireEvent.click(screen.getByRole("radio", { name: /^shared$/i }));
    await waitFor(() => {
      expect(screen.getByText("Team Drive")).toBeInTheDocument();
      expect(screen.queryByText("My Drive")).toBeNull();
    });
    fireEvent.click(screen.getByRole("radio", { name: /^per-user$/i }));
    await waitFor(() => {
      expect(screen.queryByText("Team Drive")).toBeNull();
      expect(screen.getByText("My Drive")).toBeInTheDocument();
    });
  });

  it("native rows offer View tools backed by the tools endpoint", async () => {
    const mockedListTools = connectorsApi.listTools as unknown as ReturnType<
      typeof vi.fn
    >;
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: "con-drive",
          label: "My Drive",
          subclass_slug: "drive_native",
        }),
      ],
      total: 1,
    });
    mockedListTools.mockResolvedValueOnce({
      connector_id: "con-drive",
      tools: [
        {
          key: "drive_native__search_files",
          name: "drive_native__search_files",
          description: "Search Drive files.",
          input_schema: { type: "object", properties: {} },
          scope: "canonical",
          write: false,
        },
        {
          key: "drive_native__create_file",
          name: "drive_native__create_file",
          description: "Create a file.",
          input_schema: { type: "object", properties: {} },
          scope: "canonical",
          write: true,
        },
      ],
    });
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "View tools for con-drive" }),
    );
    await waitFor(() => {
      expect(mockedListTools).toHaveBeenCalledWith("con-drive");
    });
    await screen.findByText("drive_native__search_files");
    await screen.findByText("drive_native__create_file");
    expect(screen.getByText("needs approval")).toBeInTheDocument();
  });

  it("workspace admins see management actions on shared rows they do not own", async () => {
    (useScopeOptional as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      activeWorkspace: { your_role: "admin" },
    });
    (useAuthOptional as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { id: "viewer" },
    });
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: "con-shared",
          owner: "someone-else",
          connection_mode: "shared",
          label: "Team Drive",
          subclass_slug: "drive_native",
        }),
      ],
      total: 1,
    });
    renderPanel();
    await screen.findByText("Team Drive");
    expect(
      screen.getByRole("button", { name: "Delete connector con-shared" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Sync connector con-shared" }),
    ).toBeInTheDocument();
    // Mirror config stays owner-only even for admins.
    expect(
      screen.queryByRole("button", { name: /configure connector/i }),
    ).toBeNull();
  });

  it("non-admin members see only Health and Tools on others shared rows", async () => {
    (useAuthOptional as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { id: "viewer" },
    });
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: "con-shared",
          owner: "someone-else",
          connection_mode: "shared",
          label: "Team Drive",
          subclass_slug: "drive_native",
        }),
      ],
      total: 1,
    });
    renderPanel();
    await screen.findByText("Team Drive");
    expect(
      screen.queryByRole("button", { name: /delete connector/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /sync connector/i }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Health check con-shared" }),
    ).toBeInTheDocument();
  });
});
