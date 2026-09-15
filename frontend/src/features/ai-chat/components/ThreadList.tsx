import {
  AuiIf,
  ThreadListItemMorePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAuiState,
  useThreadListItem,
  useThreadListItemRuntime,
} from "@assistant-ui/react";
import { ArchiveIcon, Loader2, MoreHorizontalIcon, PlusIcon, TrashIcon } from "lucide-react";

import { AgentSwitcher } from "./AgentSwitcher";
import { useActiveChatProvider } from "../useActiveChatProvider";
import { useChatActivity } from "../AIChatSurface";
import { useConfirm } from "../../../context/ConfirmContext";
import { useScope } from "../../../context/ScopeContext";

/**
 * Thread switcher rail — assistant-ui reference layout, Integral chrome.
 *
 * Composes:
 *   AgentSwitcher                (provider catalog + active agent picker)
 *   ThreadListPrimitive.Root
 *     ├─ ThreadListNew         "+ New Thread"
 *     └─ ThreadListPrimitive.Items
 *          └─ ThreadListItem (hover-revealed more menu: Archive, Delete)
 */
export function AIChatThreadList() {
  const provider = useActiveChatProvider();
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId ?? "";
  const { isRunning, streamingThreadIds } = useChatActivity();

  return (
    <aside
      className="
        flex h-full min-h-0 w-full flex-1 flex-col
        border-r border-[var(--border-subtle)] bg-[var(--panel)]
      "
    >
      <AgentSwitcher
        provider={provider}
        workspaceId={workspaceId}
        isStreaming={isRunning || streamingThreadIds.length > 0}
      />

      <header
        className="
          flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-3 pt-4 pb-2
        "
      >
        <span className="text-xs uppercase tracking-wide text-[var(--text-subtle)]">
          Conversations
        </span>
        <ThreadListNew />
      </header>

      <ThreadListPrimitive.Root className="flex flex-1 min-h-0 flex-col gap-1 overflow-y-auto p-2">
        <AuiIf condition={(s) => s.threads.isLoading}>
          <ThreadListSkeleton />
        </AuiIf>
        <AuiIf condition={(s) => !s.threads.isLoading}>
          <ThreadListPrimitive.Items>
            {() => <ThreadListItem />}
          </ThreadListPrimitive.Items>
        </AuiIf>
      </ThreadListPrimitive.Root>
    </aside>
  );
}

function ThreadListNew() {
  return (
    <ThreadListPrimitive.New
      aria-label="New conversation"
      title="New conversation"
      className="
        inline-flex h-7 w-7 shrink-0 items-center justify-center
        rounded-full shadow-sm
        bg-[var(--cta-bg)] text-[var(--cta-fg)]
        transition-colors hover:bg-[var(--cta-hover)]
        focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]
      "
    >
      <PlusIcon size={14} strokeWidth={2.25} />
    </ThreadListPrimitive.New>
  );
}

function ThreadListSkeleton() {
  return (
    <div className="flex flex-col gap-1">
      {Array.from({ length: 5 }, (_, i) => (
        <div
          key={i}
          role="status"
          aria-label="Loading conversations"
          className="flex h-9 items-center px-3"
        >
          <div className="h-3 w-full animate-pulse rounded-[var(--radius-input)] bg-[var(--panel-2)]" />
        </div>
      ))}
    </div>
  );
}

function ThreadGroupHeader({ label }: { label: string }) {
  return (
    <div
      className="
        sticky top-0 z-10 -mx-2 px-5 pb-1 pt-3
        bg-[var(--panel)] text-[11px] font-medium
        uppercase tracking-wide text-[var(--text-subtle)]
      "
    >
      {label}
    </div>
  );
}

function ThreadListItem() {
  const threadId = useAuiState((s) => s.threadListItem.id);
  const { threadGroups } = useChatActivity();
  const group = threadGroups.get(threadId);

  return (
    <>
      {group?.isFirst && <ThreadGroupHeader label={group.label} />}
      <ThreadListItemPrimitive.Root
        className="
          group flex h-9 items-center gap-1
          rounded-[var(--radius-input)]
          transition-colors duration-fast
          hover:bg-[var(--panel-2)] focus-visible:bg-[var(--panel-2)]
          data-[active]:bg-[var(--nav-active-bg)]
        "
      >
      <ThreadListItemPrimitive.Trigger
        className="
          flex h-full min-w-0 flex-1 items-center gap-2 px-3
          text-left text-sm text-[var(--text)]
          focus:outline-none
        "
      >
        <ThreadListItemStreamingSpinner />
        <span className="min-w-0 flex-1 truncate">
          <ThreadListItemPrimitive.Title fallback="New conversation" />
        </span>
      </ThreadListItemPrimitive.Trigger>
        <ThreadListItemMore />
      </ThreadListItemPrimitive.Root>
    </>
  );
}

/**
 * Busy indicator for a conversation row. Must read from React context — not
 * via `AuiIf` — because assistant-ui store updates alone do not fire when a
 * background stream starts or ends.
 *
 * Uses `isThreadStreaming` rather than the raw `streamingThreadIds` so a turn
 * running elsewhere (a routine, another window) shows here too. Reading the
 * list directly meant the row looked idle while the agent was working on it.
 */
function ThreadListItemStreamingSpinner() {
  const threadId = useAuiState((s) => s.threadListItem.id);
  const { isThreadStreaming } = useChatActivity();
  if (!isThreadStreaming(threadId)) return null;
  return (
    <Loader2
      size={14}
      className="shrink-0 animate-spin text-[var(--text-subtle)]"
      aria-hidden
    />
  );
}

function ThreadListItemMore() {
  const itemRuntime = useThreadListItemRuntime();
  const title = useThreadListItem((s) => s.title);
  const confirm = useConfirm();

  const handleDelete = async () => {
    const name = title?.trim();
    const ok = await confirm({
      title: "Delete conversation?",
      message: name
        ? `“${name}” will be permanently deleted. This can’t be undone.`
        : "This conversation will be permanently deleted. This can’t be undone.",
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!ok) return;
    await itemRuntime.delete();
  };

  return (
    <ThreadListItemMorePrimitive.Root>
      <ThreadListItemMorePrimitive.Trigger
        aria-label="More options"
        className="
          me-1.5 flex h-7 w-7 items-center justify-center
          rounded-[var(--radius-input)]
          text-[var(--text-subtle)] opacity-0
          transition-opacity hover:bg-[var(--panel)]
          hover:text-[var(--text)]
          group-hover:opacity-100
          data-[state=open]:bg-[var(--panel)] data-[state=open]:opacity-100
          data-[state=open]:text-[var(--text)]
          focus:outline-none focus:opacity-100
        "
      >
        <MoreHorizontalIcon size={14} />
      </ThreadListItemMorePrimitive.Trigger>
      <ThreadListItemMorePrimitive.Content
        side="bottom"
        align="start"
        className={`
          z-popover min-w-32 overflow-hidden rounded-[var(--radius-input)]
          border border-[var(--panel-border)] bg-[var(--panel)] p-1
          text-sm text-[var(--text)] shadow-[var(--shadow-pop)]
        `}
      >
        <ThreadListItemPrimitive.Archive asChild>
          <ThreadListItemMorePrimitive.Item
            className="
              flex cursor-pointer select-none items-center gap-2
              rounded-[var(--radius-input)] px-2 py-1.5
              outline-none hover:bg-[var(--panel-2)]
              focus:bg-[var(--panel-2)]
            "
          >
            <ArchiveIcon size={14} />
            Archive
          </ThreadListItemMorePrimitive.Item>
        </ThreadListItemPrimitive.Archive>
        <ThreadListItemMorePrimitive.Item
            onSelect={() => {
              void handleDelete();
            }}
            className="
              flex cursor-pointer select-none items-center gap-2
              rounded-[var(--radius-input)] px-2 py-1.5
              text-[var(--danger-fg)]
              outline-none hover:bg-[var(--danger-bg)]
              focus:bg-[var(--danger-bg)]
            "
          >
            <TrashIcon size={14} />
            Delete
          </ThreadListItemMorePrimitive.Item>
      </ThreadListItemMorePrimitive.Content>
    </ThreadListItemMorePrimitive.Root>
  );
}
