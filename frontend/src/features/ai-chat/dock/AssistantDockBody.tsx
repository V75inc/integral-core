import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Maximize2, Menu, Minimize2, X } from "lucide-react";
import { peekLastActiveChatThreadId } from "../chatHandoff";
import { useScope } from "../../../context/ScopeContext";
import { AIChatRuntimeBoundary } from "../AIChatSurface";
import { AIChatThread } from "../components/Thread";
import { AIChatThreadList } from "../components/ThreadList";
import { useActiveChatProvider } from "../useActiveChatProvider";
import { useAssistantDock } from "../../../context/AssistantDockContext";
import { InboxView } from "../inbox/InboxView";
import "../inbox/inbox.css";
import { OnboardingStrip } from "./OnboardingStrip";
import { AssistantViewTabs } from "../inbox/AssistantViewTabs";
import { useIsMdUp } from "../../../hooks/useMediaQuery";
import { useAuthOptional } from "../../../context/AuthContext";
import { LINE_ICON_STROKE } from "../../../components/ui";

/** Close affordance for the dock. Inlined here rather than kept as its own
 *  module — it has exactly one caller. */
function CloseChatButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label="Close chat"
      className="
        flex h-8 w-8 items-center justify-center rounded-full
        text-[var(--text-muted)] hover:bg-[var(--panel-2)]
        hover:text-[var(--text)] transition
        focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]
      "
    >
      <X size={16} />
    </button>
  );
}

const layoutButtonClass = "flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)] focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]";

export interface AssistantDockBodyProps {
  onClose: () => void;
  fullPage: boolean;
  onCollapse: () => void;
}

/**
 * Assistant dock body — shared runtime for thread list + active thread.
 * Conversation switching uses a slide-over drawer scoped to the dock panel
 * (not the full viewport) so it stays inside the dock's column.
 *
 * The dock shell owns positioning, sizing and the resize handle; everything
 * here is layout-agnostic, which is why the same tree also renders at
 * full-page size on `/agent`.
 */
export default function AssistantDockBody({ onClose, fullPage, onCollapse }: AssistantDockBodyProps) {
  const provider = useActiveChatProvider();
  const { scope } = useScope();
  const principal = useAuthOptional()?.user?.id;
  const location = useLocation();
  const isMdUp = useIsMdUp();
  const rail = fullPage && isMdUp;
  const workspace = scope?.workspaceId;
  const deepLink = fullPage ? new URLSearchParams(location.search).get("thread") : null;
  // Keep the boundary key across layout changes. A new explicit thread link
  // still selects a new runtime, including while this surface is already open.
  const target = useRef({
    workspace, principal, linkKey: deepLink ? location.key : null,
    threadId: deepLink ?? (fullPage ? null : peekLastActiveChatThreadId(workspace)),
  });
  if (target.current.workspace !== workspace || target.current.principal !== principal) {
    const scopeChanged = (target.current.workspace && target.current.workspace !== workspace)
      || (target.current.principal && target.current.principal !== principal);
    target.current = { workspace, principal, linkKey: location.key, threadId: scopeChanged ? null : deepLink };
  } else if (deepLink && location.key !== target.current.linkKey) {
    target.current = { workspace, principal, linkKey: location.key, threadId: deepLink };
  }
  const { onboarding, view, setView } = useAssistantDock();
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => setDrawerOpen(false), [fullPage, isMdUp]);
  useEffect(() => {
    if (!fullPage || isMdUp || !drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [fullPage, isMdUp, drawerOpen]);

  // Close the drawer before the dock on Escape.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [drawerOpen]);

  return (
    <AIChatRuntimeBoundary
      key={JSON.stringify([principal, provider.id, target.current.linkKey])}
      provider={provider}
      initialThreadId={target.current.threadId}
      startNewThread={!fullPage && onboarding && !target.current.threadId}
    >
      <div className={`relative flex h-full min-h-0 w-full bg-[var(--section-bg)] ${fullPage ? "flex-row" : "flex-col"}`}>
        {rail && <aside aria-label="Conversations" className="flex h-full w-72 shrink-0 flex-col bg-[var(--panel)] pt-3">
          <div className="min-h-0 flex-1 overflow-hidden" onClick={() => setView("chat")}>
            <AIChatThreadList />
          </div>
        </aside>}
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col"
          style={fullPage ? { paddingTop: "var(--app-topbar-height)" } : undefined}>
          <header className={fullPage
            ? "flex shrink-0 items-center justify-end gap-2 px-3 py-1"
            : "flex shrink-0 items-center justify-between gap-2 border-b border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2"}>
            {!rail && <button type="button" onClick={() => setDrawerOpen(true)}
              aria-label="Show conversations" aria-expanded={drawerOpen}
              className="mr-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-[var(--text-muted)] hover:bg-[var(--panel-2)]">
              <Menu size={14} strokeWidth={LINE_ICON_STROKE} /> Conversations
            </button>}
            {!fullPage && <div className="flex min-w-0 flex-1"><AssistantViewTabs /></div>}
            {fullPage ? (
              <button type="button" onClick={onCollapse} aria-label="Move chat to sidebar" title="Move chat to sidebar" className={layoutButtonClass}>
                <Minimize2 size={14} />
              </button>
            ) : (
              <div className="flex shrink-0 items-center gap-1">
                <Link to="/agent" aria-label="Open full chat" title="Open full chat" className={layoutButtonClass}>
                  <Maximize2 size={14} />
                </Link>
                <CloseChatButton onClose={onClose} />
              </div>
            )}
          </header>
          {!rail && drawerOpen && <button type="button" aria-label="Close conversations" onClick={() => setDrawerOpen(false)}
            className="absolute inset-0 z-40 bg-black/40" />}
          {!rail && <div role="dialog" aria-modal={drawerOpen} aria-label="Conversations" aria-hidden={!drawerOpen}
            className={`absolute inset-y-0 left-0 z-50 flex w-[min(300px,88%)] flex-col border-r border-[var(--panel-border)] bg-[var(--panel)] transition-transform duration-200 ${drawerOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full invisible"}`}
            onClick={() => { setDrawerOpen(false); setView("chat"); }}>
            <div className="flex justify-end px-2 py-1.5">
              <button type="button" onClick={() => setDrawerOpen(false)} aria-label="Close conversations" className={layoutButtonClass}>
                <X size={16} strokeWidth={LINE_ICON_STROKE} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden"><AIChatThreadList /></div>
          </div>}
          <div className="relative flex min-h-0 flex-1 flex-col">
            {view === "inbox" ? (
              <div className={fullPage ? "mx-auto h-full w-full max-w-3xl" : "h-full"}><InboxView /></div>
            ) : (
              <>
                {!fullPage && onboarding ? <OnboardingStrip /> : null}
                <AIChatThread showHeader={false} />
              </>
            )}
          </div>
        </div>
      </div>
    </AIChatRuntimeBoundary>
  );
}
