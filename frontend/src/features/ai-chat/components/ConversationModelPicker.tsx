import { isChatProfile } from "../../../api/modelProfileKinds";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { aiConfigurationApi } from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Button } from "../../../components/ui/Button";
import { HoverTooltip } from "../../../components/ui/HoverTooltip";
import { Brain, ChevronDown, X } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconButton, Select, Surface, Text } from "../../../ui";
import { useToast } from "../../../context/ToastContext";
import { LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import { useAIConfiguration } from "../../settings/hooks/useAIConfiguration";
import { useChatActivity } from "../AIChatSurface";

export function ConversationModelPicker() {
  const { activeThreadId, isRunning, activeProviderId } = useChatActivity();
  const configuration = useAIConfiguration();
  const qc = useQueryClient();
  const { showToast } = useToast();
  const [openThread, setOpenThread] = useState<string | null>(null);
  const open = Boolean(activeThreadId && openThread === activeThreadId);
  const [tooltipDismissed, setTooltipDismissed] = useState(false);
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const reasoningId = useId();
  const close = useCallback(() => {
    setOpenThread(null);
    anchor.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, []);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const trigger = anchor.current?.getBoundingClientRect();
      const content = panel.current?.getBoundingClientRect();
      if (!trigger || !content) return;
      const composer = anchor.current?.closest("form")?.getBoundingClientRect() ?? trigger;
      setPosition({
        left: Math.max(16, Math.min(trigger.left, window.innerWidth - content.width - 16)),
        top: composer.bottom + content.height + 8 < window.innerHeight - 8
          ? composer.bottom + 8 : Math.max(8, composer.top - content.height - 8),
      });
    };
    place();
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    if (panel.current) resize?.observe(panel.current);
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
    return () => { resize?.disconnect(); window.removeEventListener("resize", place); document.removeEventListener("scroll", place, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    if (position && !panel.current?.contains(document.activeElement)) panel.current?.querySelector<HTMLSelectElement>("select")?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !anchor.current?.contains(event.target) && !panel.current?.contains(event.target)) setOpenThread(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && event.target instanceof Node && (panel.current?.contains(event.target) || anchor.current?.contains(event.target))) {
        event.preventDefault(); event.stopImmediatePropagation(); close();
      }
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", escape, true); };
  }, [open, position, close]);
  const key = [
    "conversation-model",
    activeThreadId,
    configuration.data?.effective.workspace_id,
    configuration.data?.routing?.revision,
    configuration.data?.profiles.map(profile => `${profile.id}:${profile.updated_at}`).join("|"),
  ];
  const selection = useQuery({
    queryKey: key,
    queryFn: () => aiConfigurationApi.conversation(activeThreadId!),
    enabled:
      !!activeThreadId &&
      activeProviderId === "integral_native" &&
      configuration.data?.effective.editable === true,
  });
  const change = useMutation({
    mutationFn: (value: string | { role: "heavy" | "light"; effort: string | null }) => {
      if (typeof value !== "string") return aiConfigurationApi.conversation(activeThreadId!, undefined, undefined, { [value.role]: value.effort });
      const id = value;
      return aiConfigurationApi.conversation(
        activeThreadId!,
        id === "__heavy" || id === "" ? "" : id,
        id === "__heavy" ? "heavy" : id ? "profile" : "inherit",
      );
    },
    onSuccess: (result, value) => {
      qc.setQueryData(key, result);
      if (typeof value !== "string") showToast("Reasoning updated for this conversation.", "success");
    },
    onError: (error) =>
      showToast(
        errorMessageFromAxios(
          error,
          "Could not change the conversation model.",
        ),
        "error",
      ),
  });
  if (!selection.data?.editable || !configuration.data) return null;
  const chatProfiles = configuration.data.profiles.filter(isChatProfile);
  if (!chatProfiles.length && !configuration.data.effective.model && !selection.data.profile_id) return null;
  const automatic =
    configuration.data.effective.routing?.mode === "light_heavy";
  const selected = configuration.data.profiles.find(
    (p) => p.id === selection.data.profile_id,
  );
  const current =
    selection.data.profile_id ||
    (selection.data.routing_mode === "heavy" ? "__heavy" : "");
  const disabled = isRunning || change.isPending;
  const effortLabel = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
  const reasoningRoles = (["heavy", "light"] as const).filter(role => selection.data.reasoning_choices?.[role]?.efforts.length);
  const reasoningSummary = reasoningRoles.map(role => {
    const choice = selection.data.reasoning_choices![role]!;
    const override = selection.data.reasoning_efforts?.[role];
    const level = override || choice.default_effort;
    const value = level ? effortLabel(level) : "Provider default";
    return `${reasoningRoles.length > 1 ? `${effortLabel(role)}: ` : ""}${value}${!override && level ? " · Default" : ""}`;
  }).join(" / ");
  const heavyModel = configuration.data.effective.routing?.heavy_model || configuration.data.effective.model;
  const lightModel = configuration.data.effective.routing?.light_model;
  const profileName = (model: string | undefined) => configuration.data?.profiles.find(profile => profile.model === model)?.name || model;
  const workspaceModel = profileName(heavyModel) || "Not configured";
  const workspaceRoutingLabel = automatic && lightModel ? `${profileName(lightModel)} + ${workspaceModel}` : workspaceModel;
  const overrideLabel = current === "__heavy" ? "Heavy only" : selected?.name || "Saved model";
  const overrideActive = current !== "";
  return (
    <div ref={anchor} className="relative"
      onPointerEnter={() => { if (!open) setTooltipDismissed(false); }}
      onBlur={event => {
        if (!open && !event.currentTarget.contains(event.relatedTarget)) setTooltipDismissed(false);
      }}>
      <HoverTooltip label={overrideActive ? `Conversation override: ${overrideLabel}` : "Choose model and reasoning"} placement="top" disabled={open || tooltipDismissed}>
        <IconButton label="Model for this conversation" size="md"
          aria-description={overrideActive ? `Conversation override: ${overrideLabel}` : `Workspace default: ${workspaceRoutingLabel}`}
          aria-haspopup="dialog" aria-expanded={open} disabled={isRunning}
          onClick={() => { setTooltipDismissed(true); setOpenThread(value => value === activeThreadId ? null : activeThreadId); }}>
          <Brain size={17} strokeWidth={LINE_ICON_STROKE}
            color={overrideActive ? "var(--brand-accent)" : "currentColor"} aria-hidden />
        </IconButton>
      </HoverTooltip>
      {open && createPortal(
        <Surface ref={panel} role="dialog" aria-label="Chat model settings" elevation="pop" padding="md"
          className="fixed z-popover w-80 max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-y-auto"
          style={{ top: position?.top ?? 0, left: position?.left ?? 0, visibility: position ? "visible" : "hidden" }}
          onClick={event => event.stopPropagation()}
          onBlur={event => {
            if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && !anchor.current?.contains(event.relatedTarget)) setOpenThread(null);
          }}>
          <div className="flex items-start justify-between gap-3">
            <div><Text variant="heading-sm">Chat model</Text><Text variant="meta" tone="muted">Changes apply only to this chat.</Text></div>
            <IconButton label="Close model settings" size="sm" onClick={close}><X size={15} aria-hidden /></IconButton>
          </div>
          <div className="mt-4 space-y-3">
            <Surface tone="panel-2" border="none" radius="input" padding="sm">
              <Text as="p" variant="label" tone={overrideActive ? "brand" : "default"}>
                {overrideActive ? `Conversation override: ${overrideLabel}` : "Using workspace default"}
              </Text>
              <Text as="p" variant="meta" tone="muted">
                {current === "__heavy" ? "Uses the workspace’s main model only, even if automatic routing is enabled." : overrideActive ? `Overrides workspace default (${workspaceRoutingLabel}). Settings changes do not replace this chat’s selection.` : "Main model changes in Settings apply to this chat."}
              </Text>
              {overrideActive && <Button type="button" variant="outline" size="xs" className="mt-2" disabled={disabled}
                onClick={() => { panel.current?.querySelector<HTMLSelectElement>("select")?.focus(); change.mutate(""); }}>
                Use workspace default
              </Button>}
            </Surface>
            <label className="block space-y-1">
              <Text as="span" variant="label">Use model</Text>
              <Select aria-label="Use model" value={current} disabled={disabled} onChange={event => change.mutate(event.target.value)}>
                <optgroup label="Workspace configuration">
                  <option value="">{automatic ? "Automatic · Light + Heavy" : "Workspace default"}</option>
                  <option value="__heavy">Heavy only</option>
                </optgroup>
                {selected && !isChatProfile(selected) && <option value={selected.id} disabled>{selected.name} · Not a chat model</option>}
                {chatProfiles.length > 0 && <optgroup label="Saved models">
                  {chatProfiles.map(profile => <option key={profile.id} value={profile.id} disabled={profile.harness_policy?.tier === "light"}>{profile.name}{profile.harness_policy?.tier === "light" ? " · Light role only" : ""}</option>)}
                </optgroup>}
              </Select>
            </label>
            {selected?.harness_policy && !selected.harness_policy.recommended && <Text as="p" variant="meta" tone="muted">{selected.harness_policy.message}</Text>}
            <Surface tone="panel-2" border="none" radius="input" padding="sm">
              <dl className="space-y-2">
                {current === "" && automatic && <div className="flex items-start justify-between gap-4"><Text as="dt" variant="meta" tone="muted">Light</Text><Text as="dd" variant="body-sm" className="min-w-0 break-words text-right">{profileName(lightModel)}</Text></div>}
                <div className="flex items-start justify-between gap-4"><Text as="dt" variant="meta" tone="muted">{current === "" && automatic || current === "__heavy" ? "Heavy" : "Model"}</Text><Text as="dd" variant="body-sm" className="min-w-0 break-words text-right">{selected?.model || profileName(heavyModel)}</Text></div>
              </dl>
            </Surface>
            {reasoningRoles.length > 0 && <Surface border="subtle" radius="input" padding="none">
              <button type="button" aria-expanded={reasoningOpen} aria-controls={reasoningOpen ? reasoningId : undefined}
                className="flex w-full items-center justify-between gap-3 rounded-[inherit] p-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                onClick={() => setReasoningOpen(value => !value)}>
                <Text as="span" variant="body-sm" weight="medium">Reasoning</Text>
                <span className="flex min-w-0 items-center gap-2"><Text as="span" variant="meta" tone="muted" className="truncate">{reasoningSummary}</Text><ChevronDown size={14} className={reasoningOpen ? "rotate-180 shrink-0" : "shrink-0"} aria-hidden /></span>
              </button>
              {reasoningOpen && <div id={reasoningId} className="space-y-3 px-3 pb-3">
                {reasoningRoles.map(role => {
                  const choice = selection.data.reasoning_choices![role]!;
                  return <label key={role} className="block space-y-1">
                    <Text as="span" variant="label">{automatic && current === "" ? `${effortLabel(role)} model` : "Effort"}</Text>
                    <Select aria-label={`${effortLabel(role)} reasoning`} value={selection.data.reasoning_efforts?.[role] || ""} disabled={disabled}
                      onChange={event => change.mutate({ role, effort: event.target.value || null })}>
                      <option value="">Model default{choice.default_effort ? ` (${effortLabel(choice.default_effort)})` : ""}</option>
                      {choice.efforts.map(effort => <option key={effort} value={effort}>{effortLabel(effort)}</option>)}
                    </Select>
                  </label>;
                })}
              </div>}
            </Surface>}
          </div>
        </Surface>, document.body)}
    </div>
  );
}
