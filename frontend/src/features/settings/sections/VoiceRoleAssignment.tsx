import { useId, type ReactNode } from "react";

import { Button } from "../../../components/ui/Button";
import { Field } from "../../../patterns";
import { Select, Text } from "../../../ui";

export function VoiceRoleAssignment({
  label, selected, options, dirty, pending, onChange, onSave, onCancel, helper, error,
}: {
  label: string;
  selected: string;
  options: { id: string; label: string }[];
  dirty: boolean;
  pending: boolean;
  onChange: (id: string) => void;
  onSave: () => void;
  onCancel: () => void;
  helper?: ReactNode;
  error?: string;
}) {
  const id = useId();
  return <div className="space-y-2">
    <div className="flex flex-wrap items-end gap-2">
      <div className="w-full min-w-0 flex-1 basis-60">
        <Field label={label} htmlFor={id}>
          <Select id={id} value={selected} disabled={pending} onChange={event => onChange(event.target.value)}>
            {options.map(option => <option value={option.id} key={option.id}>{option.label}</option>)}
          </Select>
        </Field>
      </div>
      {dirty && <div className="flex gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>Cancel</Button>
        <Button type="button" onClick={onSave} loading={pending} disabled={pending}>Save {label.toLowerCase()}</Button>
      </div>}
    </div>
    {helper && <Text as="p" variant="meta" tone="muted">{helper}</Text>}
    {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
  </div>;
}
