import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantDockProvider } from "../../../../context/AssistantDockContext";
import { AssistantDock } from "../AssistantDock";
import { requestOpenCompanionChat } from "../../chatHandoff";

const screenSize = vi.hoisted(() => ({ desktop: true }));
vi.mock("../../../../hooks/useMediaQuery", () => ({ useIsMdUp: () => screenSize.desktop }));
vi.mock("../../../../hooks/useOverlayPresence", () => ({ useRegisterOverlay: () => {} }));
vi.mock("../../../../context/ScopeContext", () => ({ useScope: () => ({ scope: { workspaceId: "workspace" } }) }));
vi.mock("../../useActiveChatProvider", () => ({ useActiveChatProvider: () => ({ id: "integral_native" }) }));
vi.mock("../../AIChatSurface", () => ({ AIChatRuntimeBoundary: ({ children }: { children: ReactNode }) => children }));
vi.mock("../../components/ThreadList", () => ({ AIChatThreadList: () => null }));
vi.mock("../../inbox/AssistantViewTabs", () => ({ AssistantViewTabs: () => null }));
vi.mock("../../components/Thread", () => ({ AIChatThread: () => <textarea aria-label="Test composer" /> }));

function Path() {
  return <output>{useLocation().pathname}</output>;
}

function ChatDestination() {
  const navigate = useNavigate();
  return <button onClick={() => {
    navigate("/apps");
    requestOpenCompanionChat({ threadId: "thread" });
  }}>Open destination with chat</button>;
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
afterEach(cleanup);

it.each([true, false])("retains the same composer across both layouts (desktop %s)", desktop => {
  screenSize.desktop = desktop;
  localStorage.setItem("integral:assistant-dock:open", "1");
  render(<MemoryRouter initialEntries={["/apps"]}>
    <AssistantDockProvider><Path /><AssistantDock /></AssistantDockProvider>
  </MemoryRouter>);
  const composer = screen.getByRole("textbox", { name: "Test composer" });
  fireEvent.change(composer, { target: { value: "Still drafting" } });
  fireEvent.click(screen.getByRole("link", { name: "Open full chat" }));
  expect(screen.getByText("/agent")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toBe(composer);
  fireEvent.click(screen.getByRole("button", { name: "Move chat to sidebar" }));
  expect(screen.getByText("/apps")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toBe(composer);
  expect(composer).toHaveValue("Still drafting");
  fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
  expect(composer.isConnected).toBe(false);
});

it("keeps the composer when a full-page chat link opens its companion on the destination", () => {
  render(<MemoryRouter initialEntries={["/agent"]}>
    <AssistantDockProvider><ChatDestination /><AssistantDock /></AssistantDockProvider>
  </MemoryRouter>);
  const composer = screen.getByRole("textbox");
  fireEvent.click(screen.getByRole("button", { name: "Open destination with chat" }));
  expect(screen.getByRole("complementary", { name: "AI chat" })).toContainElement(composer);
});
