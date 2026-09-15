import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Maximize2, Menu, X } from "lucide-react";
import { AIChatRuntimeBoundary } from "../AIChatSurface";
import { AIChatThread } from "../components/Thread";
import { AIChatThreadList } from "../components/ThreadList";
import { useActiveChatProvider } from "../useActiveChatProvider";
import { useAssistantDock } from "../../../context/AssistantDockContext";
import { useFirstLoginOnboarding } from "../../../hooks/useFirstLoginOnboarding";
import { useAuthOptional } from "../../../context/AuthContext";
import { useQueryClient } from "@tanstack/react-query";
import { Text } from "../../../ui";
import { InboxView } from "../inbox/InboxView";
import "../inbox/inbox.css";
import { AssistantViewTabs } from "../inbox/AssistantViewTabs";
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

/**
 * First-login framing inside the dock: what to say, and a way out.
 *
 * Replaces the header + Skip that used to belong to the onboarding modal.
 * Keeping it inline means onboarding is the same surface as every later
 * conversation rather than a one-off dialog the user never sees again.
 */
function OnboardingStrip() {
  const { dismiss } = useFirstLoginOnboarding();
  const { setOnboarding } = useAssistantDock();
  const auth = useAuthOptional();
  const qc = useQueryClient();

  const handleSkip = () => {
    // Refresh the user so a completed `onboarded_at` propagates even when the
    // chat surface never signalled completion — belt and braces, same as the
    // modal did.
    auth?.refreshUser?.().catch(() => {
      /* best effort */
    });
    qc.invalidateQueries({ queryKey: ["auth", "me"] });
    setOnboarding(false);
    dismiss();
  };

  return (
    <div
      data-testid="onboarding-seed-hint"
      className="flex shrink-0 items-start gap-2 border-b border-[var(--panel-border)] px-3 py-2"
    >
      <Text variant="meta" tone="muted" as="p" className="min-w-0 flex-1 leading-normal">
        Say hi to begin — I&rsquo;ll walk you through your Apps, Tracks, theme and
        retrieval mode.
      </Text>
      <button
        type="button"
        onClick={handleSkip}
        className="shrink-0 rounded-[var(--radius-input)] px-2 py-0.5 hover:bg-[var(--panel-2)]"
      >
        <Text variant="meta" tone="subtle">Skip</Text>
      </button>
    </div>
  );
}

export interface AssistantDockBodyProps {
  onClose: () => void;
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
export default function AssistantDockBody({ onClose }: AssistantDockBodyProps) {
  const provider = useActiveChatProvider();
  const { onboarding, view, setView } = useAssistantDock();
  const [drawerOpen, setDrawerOpen] = useState(false);

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
      key={provider.id}
      provider={provider}
      startNewThread={onboarding}
    >
      <div className="flex h-full min-h-0 flex-col">
      <header
        className="
          flex shrink-0 items-center justify-between gap-2 border-b border-[var(--panel-border)]
          bg-[var(--panel)] px-3 py-2
        "
      >
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Show conversations"
          aria-expanded={drawerOpen}
          className="
            inline-flex min-w-0 shrink-0 items-center gap-1.5 rounded-md px-2 py-1.5
            text-xs font-medium text-[var(--text-muted)]
            hover:bg-[var(--panel-2)] hover:text-[var(--text)]
          "
        >
          <Menu size={14} strokeWidth={LINE_ICON_STROKE} />
          <span className="truncate">Conversations</span>
        </button>
        <div className="flex min-w-0 flex-1">
          <AssistantViewTabs />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Link
            to="/agent"
            onClick={onClose}
            aria-label="Open full chat"
            title="Open full chat"
            className="
              flex h-8 w-8 items-center justify-center rounded-full
              text-[var(--text-muted)] hover:bg-[var(--panel-2)]
              hover:text-[var(--text)] transition
              focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]
            "
          >
            <Maximize2 size={14} />
          </Link>
          <CloseChatButton onClose={onClose} />
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1 flex-col">
        {drawerOpen ? (
          <button
            type="button"
            aria-label="Close conversations"
            onClick={() => setDrawerOpen(false)}
            className="absolute inset-0 z-40 bg-black/40"
          />
        ) : null}

        <div
          role="dialog"
          aria-modal={drawerOpen}
          aria-label="Conversations"
          aria-hidden={!drawerOpen}
          className={[
            "absolute left-0 top-0 z-50 flex h-full w-[min(300px,88%)] flex-col",
            "border-r border-[var(--panel-border)] bg-[var(--panel)] shadow-2xl",
            "transition-transform duration-200 ease-out",
            drawerOpen ? "translate-x-0" : "-translate-x-full pointer-events-none",
          ].join(" ")}
          /* Any click inside dismisses the drawer — including picking a
             thread. Picking a thread from the Inbox tab means you want that
             conversation, so go there rather than dropping back onto a list
             of pending work. */
          onClick={() => {
            setDrawerOpen(false);
            setView("chat");
          }}
        >
          <div className="flex items-center justify-end px-2 py-1.5">
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close conversations"
              className="
                inline-flex h-8 w-8 items-center justify-center rounded-md
                text-[var(--text-muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]
              "
            >
              <X size={16} strokeWidth={LINE_ICON_STROKE} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <AIChatThreadList />
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col">
          {view === "inbox" ? (
            <InboxView />
          ) : (
            <>
              {onboarding ? <OnboardingStrip /> : null}
              <AIChatThread showHeader={false} />
            </>
          )}
        </div>
      </div>
      </div>
    </AIChatRuntimeBoundary>
  );
}
