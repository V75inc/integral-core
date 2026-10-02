/** Write-only secret field for app settings (API keys). */

import React from 'react';
import type { WidgetProps } from './index';

const INPUT_CLASS =
  'w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel)] ' +
  'px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] ' +
  'focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)] ' +
  'disabled:opacity-60';

export function SecretWidget(props: WidgetProps) {
  const { name, value, onChange, required, disabled, uiHints } = props;
  const stringValue = value == null ? '' : String(value);
  const placeholder =
    uiHints.placeholder ||
    (required ? 'Enter API key' : 'Leave blank to keep current key');
  return (
    <input
      id={`app-settings-field-${name}`}
      type="password"
      autoComplete="new-password"
      value={stringValue}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      required={required && !stringValue}
      disabled={disabled}
      className={INPUT_CLASS}
      data-testid={`widget-secret-${name}`}
    />
  );
}
