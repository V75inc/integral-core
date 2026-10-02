/** Single-select dropdown — Phase 10 Plan 10-05 (APP-SETTINGS-01). */

import React from 'react';
import { AppSelect } from '../../ui';
import type { WidgetProps } from './index';

export function SelectWidget(props: WidgetProps) {
  const { name, schema, value, onChange, required, disabled } = props;
  const options = (schema.enum as unknown[] | undefined) || [];
  const stringValue = value == null ? '' : String(value);

  const selectOptions = [
    ...(!required
      ? [{ value: '', label: '(none)' as React.ReactNode }]
      : stringValue === ''
        ? [{ value: '', label: 'Select…' as React.ReactNode, disabled: true }]
        : []),
    ...options.map(opt => {
      const optString = String(opt);
      return { value: optString, label: optString };
    }),
  ];

  return (
    <div data-testid={`widget-select-${name}`}>
      <AppSelect
        id={`app-settings-field-${name}`}
        className="app-input text-sm py-2 w-full"
        value={stringValue}
        onValueChange={onChange}
        disabled={disabled}
        options={selectOptions}
        aria-label={(schema.title as string | undefined) || name}
      />
    </div>
  );
}
