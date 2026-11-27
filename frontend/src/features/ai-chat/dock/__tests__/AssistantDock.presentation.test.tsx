import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantDockProvider, useAssistantDock } from "../../../../context/AssistantDockContext";
import { AssistantDock } from "../AssistantDock";
import { consumeChatHandoff, requestOpenCompanionChat } from "../../chatHandoff";

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
    requestOpenCompanionChat({ threadId: "thread", navigating: true });
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

it("keeps the live composer when a voice handoff is consumed between router renders", () => {
  function VoiceHandoff() {
    const navigate = useNavigate();
    const { open } = useAssistantDock();
    return <>
      <output>{open ? "Companion requested" : "Companion closed"}</output>
      <button onClick={() => requestOpenCompanionChat({ threadId: "voice-thread", navigating: true })}>Prepare companion</button>
      <button onClick={() => { consumeChatHandoff(); navigate("/apps/test"); }}>Render app</button>
    </>;
  }
  render(<MemoryRouter initialEntries={["/agent"]}>
    <AssistantDockProvider><VoiceHandoff /><AssistantDock /></AssistantDockProvider>
  </MemoryRouter>);
  const composer = screen.getByRole("textbox");
  fireEvent.click(screen.getByRole("button", { name: "Prepare companion" }));
  expect(screen.getByText("Companion requested")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Render app" }));
  expect(screen.getByRole("complementary", { name: "AI chat" })).toContainElement(composer);
});

it.each([true, false])("pins a live full-page call on ordinary Home and app navigation (desktop %s)", async desktop => {
  const { claimVoiceSession, releaseVoiceSession } = await import("../../nativeVoiceSession");
  const { act } = await import("@testing-library/react");
  const owner = Symbol();
  const close = vi.fn(() => releaseVoiceSession(owner));
  screenSize.desktop = desktop;
  function Navigation() {
    const navigate = useNavigate();
    return <><button onClick={() => navigate("/")}>Home</button>
      <button onClick={() => navigate("/apps/test")}>App</button>
      <button onClick={() => navigate("/agent?thread=voice-thread")}>Same chat</button></>;
  }
  render(<MemoryRouter initialEntries={["/agent"]}>
    <AssistantDockProvider><Navigation /><AssistantDock /></AssistantDockProvider>
  </MemoryRouter>);
  try {
    const composer = screen.getByRole("textbox");
    act(() => { claimVoiceSession({ owner, surface: "dock", workspaceId: "workspace", threadId: "voice-thread", close }); });
    fireEvent.click(screen.getByRole("button", { name: "Home" }));
    expect(screen.getByRole("complementary", { name: "AI chat" })).toContainElement(composer);
    fireEvent.click(screen.getByRole("button", { name: "App" }));
    expect(screen.getByRole("textbox")).toBe(composer);
    fireEvent.click(screen.getByRole("button", { name: "Same chat" }));
    expect(screen.getByRole("region", { name: "AI chat" })).toContainElement(composer);
    expect(close).not.toHaveBeenCalled();
  } finally { act(() => releaseVoiceSession(owner)); }
});
