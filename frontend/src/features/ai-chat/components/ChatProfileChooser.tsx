import { useId, useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import { LINE_ICON_STROKE } from "../../../components/ui";
import { AgentAvatar } from "../../../components/ui/AgentAvatar";
import { ALL_WORKSPACE_ASSISTANTS, WORKSPACE_ASSISTANT, type ChatAgentIdentity } from "../../../api/chatAssistantIdentity";
import { Surface, Text } from "../../../ui";
import { useChatActivity } from "../AIChatSurface";

const ALL_HISTORY: ChatAgentIdentity = {
  key: ALL_WORKSPACE_ASSISTANTS,
  name: "All workspace assistants",
  description: "",
  icon: "history",
};

function AssistantMenuRow({ identity, onSelect }: { identity: ChatAgentIdentity; onSelect: () => void }) {
  const id = useId();
  return <Menu.RadioItem
    value={identity.key}
    textValue={identity.name}
    aria-labelledby={id}
    aria-describedby={identity.description ? `${id}-description` : undefined}
    onSelect={onSelect}
    className="flex min-w-0 cursor-pointer items-center gap-2 rounded-[var(--radius-input)] px-2 py-1.5 outline-none interactive-panel-menu-item"
  >
    <AgentAvatar icon={identity.icon} avatar={identity.avatar} />
    <span className="flex min-w-0 flex-1 flex-col">
      <Text as="span" id={id} variant="body-sm" weight="medium" className="break-words">{identity.name}</Text>
      {identity.description && <Text as="span" id={`${id}-description`} variant="meta" tone="subtle" truncate>{identity.description}</Text>}
    </span>
    <Menu.ItemIndicator><Check size={14} strokeWidth={LINE_ICON_STROKE} aria-hidden="true" /></Menu.ItemIndicator>
  </Menu.RadioItem>;
}

export function ChatProfileChooser() {
  const { activeThreadId, threads, switchProfile, profileSelectionReady, profileChoices, profileFilter, workspaceAssistant } = useChatActivity();
  const [error, setError] = useState("");
  const available = profileChoices ?? [];
  const thread = threads.find(item => item.id === activeThreadId);
  const base = workspaceAssistant ?? WORKSPACE_ASSISTANT;
  const current = thread ? thread.agent_profile_key || base.key : profileFilter ?? base.key;
  const specialist = available.find(profile => profile.key === current);
  const active = current === ALL_HISTORY.key && available.length ? ALL_HISTORY
    : current === base.key || !available.length ? base
    : specialist ?? thread?.agent_profile ?? { key: current, name: "Unavailable profile", description: "", icon: "integral" };
  const canPick = Boolean(switchProfile && available.length);
  const triggerClass = "flex w-full items-center gap-2.5 rounded-[var(--radius-input)] px-2 py-2 text-left";
  const identity = <>
    <AgentAvatar icon={active.icon} avatar={active.avatar} />
    <span className="flex min-w-0 flex-1 flex-col leading-tight">
      <Text truncate weight="medium">{active.name}</Text>
      {active.description && <Text variant="meta" tone="subtle" truncate>{active.description}</Text>}
    </span>
  </>;
  async function select(key: string) {
    setError("");
    try {
      await switchProfile?.(key);
    } catch {
      setError("This assistant could not be opened.");
    }
  }

  return (
    <div className="border-b border-[var(--panel-border)] px-2 py-3" onClick={event => event.stopPropagation()}>
      {canPick ? <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button type="button" aria-label={`Active agent: ${active.name}`} disabled={!profileSelectionReady}
            className={`${triggerClass} interactive-panel-hover focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]`}>
            {identity}
            <Text as="span" tone="subtle" className="shrink-0"><ChevronDown aria-hidden="true" size={14} strokeWidth={LINE_ICON_STROKE} /></Text>
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content asChild align="start" sideOffset={4} collisionPadding={12}>
          <Surface radius="input" elevation="pop"
            className="z-popover max-h-[var(--radix-dropdown-menu-content-available-height)] w-[min(20rem,calc(100vw-1.5rem))] overflow-y-auto p-1">
            <Menu.RadioGroup value={active.key}>
              <AssistantMenuRow identity={ALL_HISTORY} onSelect={() => void select(ALL_HISTORY.key)} />
              <Menu.Separator className="my-1 border-t border-[var(--border-subtle)]" />
              <AssistantMenuRow identity={base} onSelect={() => void select(base.key)} />
              {available.map(profile => <AssistantMenuRow key={profile.key} identity={profile} onSelect={() => void select(profile.key)} />)}
            </Menu.RadioGroup>
          </Surface>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root> : <div className={triggerClass}>{identity}</div>}
      {error && <Text role="alert" variant="body-sm">{error}</Text>}
    </div>
  );
}
