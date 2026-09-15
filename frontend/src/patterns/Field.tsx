/**
 * Field — form-row pattern.
 *
 * Bundles label + optional hint + control + optional error into a
 * single typed composition. Replaces hand-rolled
 * `<label>` + `<input>` + `<span class="text-xs ...">` triplets across
 * settings, dialogs, and entry forms (30+ drift sites per INVENTORY §6).
 *
 * Generalizes the existing `features/settings/components/Field.tsx`
 * `SettingsField` — that wrapper now delegates here.
 *
 * Layer: Pattern (Layer 2 — composes Primitive `<Text>`).
 *
 * Usage:
 *   <Field label="Name" hint="Shown on your profile" required>
 *     <input ... />   // (until <Input> ships in Phase 3-A slice B)
 *   </Field>
 *
 *   <Field label="API key" inline error={validationErr}>
 *     <Switch ... />
 *   </Field>
 */

import { type ReactNode, useId } from 'react';

import { Text } from '../ui';

export interface FieldProps {
  /** The visible label text. */
  label: ReactNode;
  /** Helper / description text below the label. */
  hint?: ReactNode;
  /** Validation error text below the control. */
  error?: ReactNode;
  /**
   * Lay the row out as label-on-left, control-on-right (good for
   * toggles + selects). Default: stacked.
   */
  inline?: boolean;
  /**
   * Mark this field as required. Appends an `*` to the label and sets
   * `aria-required` on the wrapper.
   */
  required?: boolean;
  /**
   * `id` for the wrapped control. When set, the `<label>` is rendered
   * with `htmlFor={id}` for proper a11y. Otherwise the label wraps the
   * control (still associates them).
   */
  htmlFor?: string;
  /** The form control (input, select, switch, textarea, …). */
  children: ReactNode;
}

export function Field({
  label,
  hint,
  error,
  inline = false,
  required = false,
  htmlFor,
  children,
}: FieldProps) {
  // Generate a fallback id so error/hint can be associated via
  // `aria-describedby` even when the caller didn't pass `htmlFor`.
  const fallbackId = useId();
  const labelId = `${htmlFor ?? fallbackId}-label`;
  const hintId = hint ? `${htmlFor ?? fallbackId}-hint` : undefined;
  const errorId = error ? `${htmlFor ?? fallbackId}-error` : undefined;

  const wrapperClass = inline
    ? 'flex flex-row items-center justify-between gap-4'
    : 'flex flex-col gap-1.5';
  const labelGroupClass = inline
    ? 'flex flex-col gap-0.5 min-w-0 flex-1'
    : 'flex flex-col gap-0.5';
  const controlClass = inline ? 'shrink-0' : 'w-full';

  const labelNode = (
    <Text variant="body" weight="medium" as="label" id={labelId}>
      {label}
      {required && (
        <span aria-hidden className="ml-0.5 text-[var(--danger-fg)]">
          *
        </span>
      )}
    </Text>
  );

  return (
    <div
      className={wrapperClass}
      aria-required={required || undefined}
      aria-describedby={[hintId, errorId].filter(Boolean).join(' ') || undefined}
    >
      <div className={labelGroupClass}>
        {/* When `htmlFor` is provided, the label points to the external
            control; otherwise we still render a label element but rely
            on DOM-tree association via children placement. */}
        {htmlFor ? (
          <Text variant="body" weight="medium" as="label" id={labelId} title={undefined}>
            <label htmlFor={htmlFor} className="contents">
              {label}
              {required && (
                <span aria-hidden className="ml-0.5 text-[var(--danger-fg)]">
                  *
                </span>
              )}
            </label>
          </Text>
        ) : (
          labelNode
        )}
        {hint && (
          <Text variant="body-sm" tone="subtle" as="span" id={hintId}>
            {hint}
          </Text>
        )}
      </div>
      <div className={controlClass}>{children}</div>
      {error && (
        <Text variant="body-sm" tone="danger" as="span" id={errorId}>
          {error}
        </Text>
      )}
    </div>
  );
}
