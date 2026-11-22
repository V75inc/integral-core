import type { ReactNode } from "react";

import { Button } from "../../../components/ui/Button";
import { Text } from "../../../ui";
import { ModelAssignmentField } from "./ModelAssignmentField";

export function VoiceRoleAssignment({
  label, active, selected, options, dirty, pending, onChange, onSave, onCancel, helper, error, secondary,
}: {
  label: string;
  active?: string;
  selected: string;
  options: { id: string; label: string }[];
  dirty: boolean;
  pending: boolean;
  onChange?: (id: string) => void;
  onSave: () => void;
  onCancel: () => void;
  helper?: ReactNode;
  error?: string;
  secondary?: ReactNode;
}) {
  return <div className="space-y-2">
    <ModelAssignmentField
      label={label} active={active} value={selected} disabled={pending}
      onChange={onChange} secondary={secondary}
      options={options.map(option => <option value={option.id} key={option.id}>{option.label}</option>)}
    />
    {dirty && <div className="flex justify-end gap-2">
      <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>Cancel</Button>
      <Button type="button" onClick={onSave} loading={pending} disabled={pending}>Save {label.toLowerCase()}</Button>
    </div>}
    {helper && <Text as="p" variant="meta" tone="muted">{helper}</Text>}
    {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
  </div>;
}
