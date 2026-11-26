import { useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Popover } from "radix-ui";
import { ArrowLeft, Check, ChevronRight, Hand, ListChecks, Settings2, ShieldAlert, ShieldCheck } from "lucide-react";
import { apiClient } from "../../../api";
import { WORKSPACE_ASSISTANT } from "../../../api/chatAssistantIdentity";
import { tracksApi } from "../../../api/tracks";
import { Button } from "../../../components/ui/Button";
import { HoverTooltip } from "../../../components/ui/HoverTooltip";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { IconButton, Surface, Text } from "../../../ui";
import { useChatActivity } from "../AIChatSurface";

const modes = {
  ask: { label: "Ask for approval", description: "Review each change.", icon: Hand },
  auto: { label: "Approve for me", description: "Allow simple edits in private lists you own.", icon: ShieldCheck },
  full: { label: "Full access", description: "Allow record edits across all apps you can edit.", icon: ShieldAlert },
  custom: { label: "Custom", description: "Choose which private lists can be edited.", icon: ListChecks },
} as const;
type Mode = keyof typeof modes;
interface Permission { mode: Mode; custom_track_ids: string[]; revision: string }

export function ResidentPermissions() {
  const activity = useChatActivity();
  const user = useAuthOptional()?.user?.id;
  const workspace = useScopeOptional()?.scope?.workspaceId;
  const thread = activity.threads.find(row => row.id === activity.activeThreadId);
  const profile = thread ? thread.agent_profile_key : activity.profileFilter;
  if (!user || !workspace || activity.activeProviderId !== "integral_native" || (profile && profile !== WORKSPACE_ASSISTANT.key)) return null;
  return <PermissionPicker key={JSON.stringify([user, workspace])} user={user} workspace={workspace} />;
}

const rowStyle = "flex w-full items-center gap-2 px-2 py-2 text-left";
const tick = <Check size={16} aria-hidden className="shrink-0" />;

export function PermissionPicker({ workspace, user }: { workspace: string; user: string }) {
  const [open, setOpen] = useState(false);
  const [chooseList, setChooseList] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const [draftRevision, setDraftRevision] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const listAction = useRef<HTMLButtonElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const previousPage = useRef(false);
  useLayoutEffect(() => {
    if (open && chooseList !== previousPage.current) {
      (chooseList ? back : listAction).current?.focus();
    }
    previousPage.current = chooseList;
  }, [open, chooseList]);

  const path = `/workspaces/${encodeURIComponent(workspace)}/resident-permission`;
  const policy = useQuery({
    queryKey: ["resident-permission", user, workspace],
    queryFn: async () => (await apiClient.get<Permission>(path)).data,
    staleTime: 30_000,
  });
  const lists = useQuery({
    queryKey: ["resident-permission-tracks", user, workspace],
    queryFn: () => tracksApi.list({ workspace_id: workspace, skipEntryCounts: true }),
    enabled: open,
  });
  const choices = (lists.data ?? []).filter(row => row.resident_permission_eligible && row.workspace_id === workspace);
  const current = policy.data?.custom_track_ids;
  const mode = policy.isError ? undefined : policy.data?.mode;
  const triggerLabel = mode ? `Permissions · ${modes[mode].label}` : "Permissions";
  const locked = !current || busy || policy.isFetching || policy.isError;
  const groups = new Map<string, { name: string; rows: typeof choices }>();
  for (const row of choices) {
    const key = row.app?.id ?? "";
    const group = groups.get(key) ?? { name: row.app?.name || "Outside apps", rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  function editSelection() {
    if (!current || !policy.data) return;
    setDraft(current.filter(id => choices.some(row => row.id === id)));
    setDraftRevision(policy.data.revision);
    setError("");
    setChooseList(true);
  }
  function toggle(ids: string[], checked: boolean) {
    setDraft(existing => checked
      ? Array.from(new Set([...existing, ...ids]))
      : existing.filter(id => !ids.includes(id)));
  }
  async function save(mode: Mode, revision: string, track_ids?: string[]) {
    if (locked) return;
    setBusy(true);
    setError("");
    try {
      await apiClient.put(path, { mode, revision, ...(track_ids === undefined ? {} : { track_ids }) });
      await policy.refetch({ throwOnError: true });
      setChooseList(false);
    } catch {
      setError("Couldn’t save. Reopen Permissions to review the current selection.");
      await policy.refetch();
    } finally { setBusy(false); }
  }

  return <Popover.Root open={open} onOpenChange={value => {
    setOpen(value);
    if (value) void policy.refetch();
    if (!value) { setChooseList(false); setError(""); }
  }}>
    <HoverTooltip label={triggerLabel} placement="top" disabled={open}>
      <Popover.Trigger asChild>
        <Button type="button" size="xs" variant="ghost" className="h-10 min-w-10 rounded-full"
          aria-label={triggerLabel} icon={<Settings2 size={16} aria-hidden />}>
          {mode && <span className="composer-action-label">{modes[mode].label}</span>}
        </Button>
      </Popover.Trigger>
    </HoverTooltip>
    <Popover.Portal>
      <Popover.Content asChild aria-label="Permissions" side="top" align="end" sideOffset={8} collisionPadding={12}>
        <Surface padding="sm" elevation="pop" className="z-popover flex w-72 max-w-[var(--radix-popover-content-available-width)] max-h-[var(--radix-popover-content-available-height)] flex-col overflow-auto overscroll-contain">
          {chooseList ? <>
            <div className="flex items-center gap-1 px-1 pb-1">
              <IconButton ref={back} label="Back to permissions" size="sm" onClick={() => setChooseList(false)}><ArrowLeft size={15} /></IconButton>
              <Text variant="heading-sm">Custom approvals</Text>
            </div>
            <Text as="p" variant="meta" tone="muted" className="px-2 pb-2">Choose apps or individual private lists. New lists will still ask.</Text>
            {current?.some(id => !choices.some(row => row.id === id)) &&
              <Text as="p" variant="meta" tone="muted" className="px-2 pb-1">Saving removes lists that are no longer eligible.</Text>}
            {lists.isSuccess && !choices.length && <Text variant="meta" className="px-2 pb-1">No eligible private lists.</Text>}
            {draftRevision !== policy.data?.revision && <Text role="status" variant="meta" className="px-2 pb-1">Permissions changed. Go back and reopen this selection.</Text>}
            <div className="min-h-0 overflow-auto overscroll-contain p-1">
              {Array.from(groups, ([key, { name, rows }]) => {
                const ids = rows.map(row => row.id);
                const count = ids.filter(id => draft.includes(id)).length;
                return <fieldset key={key} disabled={locked || lists.isError || lists.isFetching} className="mb-2">
                  <legend className="sr-only">{name}</legend>
                  <Surface as="label" tone="transparent" border="none" radius="input" hoverTone="panel-2" className={rowStyle}>
                    <input type="checkbox" className="resident-permission-checkbox h-4 w-4 shrink-0 accent-[var(--brand-accent)]" checked={count === ids.length} aria-label={`All lists in ${name}`}
                      ref={el => { if (el) el.indeterminate = count > 0 && count < ids.length; }}
                      onChange={event => toggle(ids, event.target.checked)} />
                    <Text variant="body-sm" weight="semibold" className="min-w-0 break-words">{name}</Text>
                  </Surface>
                  {rows.map(row => <Surface as="label" key={row.id} tone="transparent" border="none" radius="input" hoverTone="panel-2" className={`${rowStyle} pl-6`}>
                    <input type="checkbox" className="resident-permission-checkbox h-4 w-4 shrink-0 accent-[var(--brand-accent)]" checked={draft.includes(row.id)} onChange={event => toggle([row.id], event.target.checked)} />
                    <Text variant="body-sm" className="min-w-0 break-words">{row.title}</Text>
                  </Surface>)}
                </fieldset>;
              })}
            </div>
            <div className="flex shrink-0 items-center justify-between gap-2 border-t border-[var(--panel-border)] px-2 pt-2">
              <Text variant="meta" tone="muted">{draft.length} selected</Text>
              <Button size="sm" disabled={locked || lists.isError || lists.isFetching ||
                draftRevision !== policy.data?.revision || (mode === "custom" && draft.length === current?.length && draft.every(id => current.includes(id)))}
                onClick={() => void save("custom", draftRevision, draft)}>{mode === "custom" ? "Save" : "Use Custom"}</Button>
            </div>
          </> : <>
            <div className="flex flex-wrap items-baseline justify-between gap-x-2 px-2 pb-2 pt-1">
              <Text variant="heading-sm">Permissions</Text><Text variant="meta" tone="muted">Chat &amp; voice</Text>
            </div>
            {(Object.keys(modes) as Mode[]).map(key => {
              const option = modes[key];
              const selected = mode === key;
              return <button type="button" key={key} ref={key === "custom" ? listAction : undefined}
                className="w-full rounded-[var(--radius-input)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--brand-accent-line)] disabled:opacity-60"
                disabled={locked || (key === "custom" && (lists.isFetching || lists.isError))}
                aria-pressed={selected} onClick={() => {
                  if (key === "custom") editSelection();
                  else if (!selected && policy.data) void save(key, policy.data.revision);
                }}>
                <Surface as="span" tone={selected ? "panel-2" : "transparent"} border="none" radius="input" hoverTone="panel-2" className={rowStyle}>
                  <option.icon size={16} aria-hidden className={key === "full" ? "shrink-0 text-[var(--warn-fg)]" : "shrink-0"} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <Text as="span" variant="body-sm" weight="semibold">{option.label}</Text>
                    <Text as="span" variant="meta" tone="muted">{key === "custom" && current?.length
                      ? `${current.length} saved ${current.length === 1 ? "list" : "lists"}` : option.description}</Text>
                  </span>
                  {selected && tick}{key === "custom" && <ChevronRight size={15} aria-hidden />}
                </Surface>
              </button>;
            })}
          </>}
          {(busy || error || policy.isError || lists.isError) && <Text as="p" variant="meta" role="status" className="px-2 py-1">
            {busy ? "Saving…" : error || "Couldn’t load permissions. Reopen to retry."}
          </Text>}
        </Surface>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>;
}
