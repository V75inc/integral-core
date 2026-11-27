import { useId, type ReactNode } from "react";

import type { ModelConnection, ModelProfile } from "../../../api/aiConfiguration";
import { Field } from "../../../patterns";
import { Select, Text } from "../../../ui";

export function profileAssignmentLabel(profile: ModelProfile, connections: ModelConnection[]) {
  const account = connections.find(item => item.id === profile.connection_id)?.name;
  return [profile.name, account, profile.model !== profile.name ? profile.model : undefined]
    .filter(Boolean).join(" · ");
}

/** The same role label, active value, and full-width selector for every model assignment. */
export function ModelAssignmentField({
  label, active, value, onChange, options, disabled = false, secondary,
}: {
  label: string;
  active?: ReactNode;
  value: string;
  onChange?: (value: string) => void;
  options: ReactNode;
  disabled?: boolean;
  secondary?: ReactNode;
}) {
  const id = useId();
  const current = active ? <>Active: {active}</> : undefined;
  return <div className="w-full min-w-0 space-y-2">
    {onChange ? <Field label={label} htmlFor={id} hint={current}>
      <Select id={id} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
        {options}
      </Select>
    </Field> : <div className="flex flex-col gap-0.5">
      <Text as="p" variant="body" weight="medium">{label}</Text>
      {current && <Text as="p" variant="body-sm" tone="subtle" className="break-words">{current}</Text>}
    </div>}
    {secondary && <div className="flex flex-wrap justify-end gap-2">{secondary}</div>}
  </div>;
}
