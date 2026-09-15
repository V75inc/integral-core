import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";

import { AgentSwitcher } from "../AgentSwitcher";

vi.mock("../../useAgentCatalog", () => ({
  useAgentCatalog: vi.fn(),
}));
import { useAgentCatalog } from "../../useAgentCatalog";

const mockProvider = {
  id: "jvagent",
  label: "jvagent",
  serverPersisted: true,
  capabilities: {
    reasoning: true,
    tools: true,
    attachments: true,
    vision: false,
    voice: false,
  },
  streamTurn: async function* () {},
  listAgents: async () => [],
} as const;

describe("AgentSwitcher", () => {
  it("renders nothing when catalog is empty", () => {
    (useAgentCatalog as any).mockReturnValue({
      agents: [],
      activeAgent: null,
      isLoading: false,
      error: null,
      switchAgent: vi.fn(),
      preferenceAgentId: null,
    });
    const { container } = render(
      <AgentSwitcher provider={mockProvider as any} workspaceId="w1" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders active agent's name and role label, opens popover on click", () => {
    (useAgentCatalog as any).mockReturnValue({
      agents: [
        {
          id: "iris",
          name: "Iris",
          description: "Friendly",
          role_label: "ASSISTANT",
        },
        { id: "aiva", name: "Aiva", description: "Sales" },
      ],
      activeAgent: {
        id: "iris",
        name: "Iris",
        description: "Friendly",
        role_label: "ASSISTANT",
      },
      isLoading: false,
      error: null,
      switchAgent: vi.fn(),
      preferenceAgentId: null,
    });
    render(<AgentSwitcher provider={mockProvider as any} workspaceId="w1" />);
    expect(screen.getByText("Iris")).toBeInTheDocument();
    expect(screen.getByText("ASSISTANT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /active agent/i }));
    expect(screen.getByText("Aiva")).toBeInTheDocument();
    expect(screen.getByText("Sales")).toBeInTheDocument();
    expect(screen.getByText(/Integral staging/i)).toBeInTheDocument();
  });

  it("calls switchAgent when a different agent is picked", () => {
    const switchAgent = vi.fn();
    (useAgentCatalog as any).mockReturnValue({
      agents: [
        { id: "iris", name: "Iris" },
        { id: "aiva", name: "Aiva" },
      ],
      activeAgent: { id: "iris", name: "Iris" },
      isLoading: false,
      error: null,
      switchAgent,
      preferenceAgentId: null,
    });
    render(<AgentSwitcher provider={mockProvider as any} workspaceId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: /active agent/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /aiva/i }));
    expect(switchAgent).toHaveBeenCalledWith("aiva");
  });

  it("is disabled when isStreaming prop is true", () => {
    (useAgentCatalog as any).mockReturnValue({
      agents: [
        { id: "iris", name: "Iris" },
        { id: "aiva", name: "Aiva" },
      ],
      activeAgent: { id: "iris", name: "Iris" },
      isLoading: false,
      error: null,
      switchAgent: vi.fn(),
      preferenceAgentId: null,
    });
    render(
      <AgentSwitcher
        provider={mockProvider as any}
        workspaceId="w1"
        isStreaming
      />,
    );
    expect(
      screen.getByRole("button", { name: /active agent/i }),
    ).toBeDisabled();
  });

  it("shows Echo as a static dev/smoke harness when catalog is empty", () => {
    (useAgentCatalog as any).mockReturnValue({
      agents: [],
      activeAgent: null,
      isLoading: false,
      error: null,
      switchAgent: vi.fn(),
      preferenceAgentId: null,
    });
    const echoProvider = {
      ...mockProvider,
      id: "mock-echo",
      label: "Echo (dev/smoke)",
    };
    render(
      <AgentSwitcher provider={echoProvider as any} workspaceId="w1" />,
    );
    expect(screen.getByText("Echo")).toBeInTheDocument();
    expect(screen.getByText(/dev\/smoke harness/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /active agent/i }),
    ).toBeDisabled();
  });
});
