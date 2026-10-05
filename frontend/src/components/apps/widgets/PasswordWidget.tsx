/** Masked secret input — write-only API keys and similar App settings. */

import React from 'react';
import type { WidgetProps } from './index';

const INPUT_CLASS =
  'w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel)] ' +
  'px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] ' +
  'focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)] ' +
  'disabled:opacity-60';

export function PasswordWidget(props: WidgetProps) {
  const { name, value, onChange, required, disabled, uiHints } = props;
  const stringValue = value == null ? '' : String(value);
  return (
    <input
      id={`app-settings-field-${name}`}
      type="password"
      value={stringValue}
      onChange={e => onChange(e.target.value)}
      placeholder={uiHints.placeholder}
      required={required}
      disabled={disabled}
      autoComplete="off"
      className={INPUT_CLASS}
      data-testid={`widget-password-${name}`}
    />
  );
}
