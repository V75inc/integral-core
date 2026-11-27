import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation, useNavigate, type RouteObject } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ConfirmProvider } from "../../../context/ConfirmContext";
import { AssistantDockProvider, useAssistantDock } from "../../../context/AssistantDockContext";
import { claimVoiceSession, peekVoiceSession, releaseVoiceSession } from "../nativeVoiceSession";
import { VoiceRouteGuard } from "../VoiceRouteGuard";

vi.mock("../../../hooks/useMediaQuery", () => ({ useIsMdUp: () => true }));

const routes: RouteObject[] = [
  { id: "workspace-shell", path: "/", children: [
    { index: true }, { path: "agent" }, { path: "apps/:id" }, { path: "workspaces/:workspaceId" },
  ] }, { path: "/login" }, { path: "*" },
];

function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const dock = useAssistantDock();
  return <>
    <VoiceRouteGuard routes={routes} />
    <output aria-label="Path">{location.pathname + location.search}</output>
    <button onClick={() => navigate("/")}>Home</button>
    <button onClick={() => navigate("/apps/test")}>App</button>
    <button onClick={() => navigate("/login")}>Login</button>
    <button onClick={() => navigate("/agent?thread=other")}>Other thread</button>
    <button onClick={() => navigate("/workspaces/other")}>Other workspace</button>
    <button onClick={() => navigate(-1)}>Back</button>
    <button onClick={dock.closeDock}>Close dock</button>
    <button onClick={() => dock.setView("inbox")}>Inbox</button>
    <output aria-label="Dock">{dock.open ? dock.view : "closed"}</output>
  </>;
}

function setup(surface: "dock" | "home" = "dock", entries = ["/login", "/agent?thread=here"]) {
  const router = createMemoryRouter([{ path: "*", element: <ConfirmProvider>
    <AssistantDockProvider><Shell /></AssistantDockProvider>
  </ConfirmProvider> }], { initialEntries: entries });
  render(<RouterProvider router={router} />);
  const owner = Symbol();
  const close = vi.fn(() => releaseVoiceSession(owner));
  act(() => { claimVoiceSession({ owner, surface, workspaceId: "here", threadId: "here", close }); });
  return close;
}
afterEach(() => {
  cleanup();
  const voice = peekVoiceSession();
  if (voice) releaseVoiceSession(voice.owner);
  localStorage.clear();
});
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const path = () => screen.getByLabelText("Path");
const prompt = () => screen.getByRole("dialog", { name: "End voice call?" });

it("allows ordinary internal navigation and POP without prompting or ending the call", async () => {
  const close = setup();
  click("Home");
  await waitFor(() => expect(path()).toHaveTextContent(/^\/$/));
  click("App");
  await waitFor(() => expect(path()).toHaveTextContent("/apps/test"));
  click("Back");
  await waitFor(() => expect(path()).toHaveTextContent(/^\/$/));
  expect(close).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Dock")).toHaveTextContent("chat");
});

it.each(["Login", "Other thread", "Other workspace", "Back", "Close dock", "Inbox"])(
  "requires confirmation for %s, cancel retains the call and confirm closes exactly once", async action => {
    const close = setup();
    click(action);
    await waitFor(() => expect(prompt()).toBeVisible());
    click("Stay in call");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(path()).toHaveTextContent("/agent?thread=here");
    expect(screen.getByLabelText("Dock")).toHaveTextContent("chat");
    expect(close).not.toHaveBeenCalled();
    click(action);
    await waitFor(() => expect(prompt()).toBeVisible());
    click("End call and leave");
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(peekVoiceSession()).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  }
);

it("warns when leaving the separate Home composer rather than silently ending its call", async () => {
  const close = setup("home", ["/"]);
  click("App");
  await waitFor(() => expect(prompt()).toBeVisible());
  click("Stay in call");
  expect(close).not.toHaveBeenCalled();
  expect(path()).toHaveTextContent(/^\/$/);
});

it("does not let a pending old-call confirmation end a replacement call", async () => {
  const close = setup();
  click("Login");
  await waitFor(() => expect(prompt()).toBeVisible());
  const old = peekVoiceSession()!;
  const owner = Symbol();
  const replacement = vi.fn(() => releaseVoiceSession(owner));
  act(() => {
    releaseVoiceSession(old.owner);
    claimVoiceSession({ ...old, owner, close: replacement });
  });
  click("End call and leave");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(path()).toHaveTextContent("/agent?thread=here");
  expect(replacement).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
});

it("uses the Integral dialog for an app-owned reload and only acts after cleanup", async () => {
  const { guardAppDeparture } = await import("../useVoiceDepartureGuard");
  const close = setup();
  const reload = vi.fn();
  act(() => guardAppDeparture(reload));
  await waitFor(() => expect(prompt()).toBeVisible());
  click("Stay in call");
  expect(reload).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
  act(() => guardAppDeparture(reload));
  await waitFor(() => expect(prompt()).toBeVisible());
  click("End call and leave");
  await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  expect(close).toHaveBeenCalledTimes(1);
  expect(peekVoiceSession()).toBeNull();
});
