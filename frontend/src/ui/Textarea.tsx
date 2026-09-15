/**
 * Textarea — multi-line text input primitive.
 *
 * Wraps `<textarea>` with the canonical `app-input` styling. Mirrors
 * the `<Input>` API for size/invalid/monospace. Use inside `<Field>`
 * for label+hint+error composition.
 *
 * Layer: Primitive (Layer 1).
 *
 * Usage:
 *   <Field label="Description" htmlFor="desc">
 *     <Textarea id="desc" rows={4} value={...} onChange={...} />
 *   </Field>
 */

import { forwardRef, type TextareaHTMLAttributes } from 'react';

export type TextareaSize = 'sm' | 'md' | 'lg';

const SIZE_CLASSES: Record<TextareaSize, string> = {
  sm: 'text-xs',
  md: '',
  lg: 'text-base',
};

export interface TextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'size'> {
  size?: TextareaSize;
  invalid?: boolean;
  monospace?: boolean;
  /** Disable resize handle. Default: `false`. */
  noResize?: boolean;
  /**
   * Layout-positioning classes only (max-width caps, grid placement).
   * NEVER size/spacing/color — use typed props.
   */
  className?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea(
    {
      size = 'md',
      invalid = false,
      monospace = false,
      noResize = false,
      className,
      rows = 3,
      ...rest
    },
    ref,
  ) {
    const classes = [
      'app-input',
      SIZE_CLASSES[size],
      invalid
        ? 'border-[var(--danger-fg)] focus:shadow-[0_0_0_2px_var(--danger-bg)]'
        : '',
      monospace ? 'font-mono' : '',
      noResize ? 'resize-none' : '',
      className ?? '',
    ]
      .filter(Boolean)
      .join(' ');
    return (
      <textarea
        ref={ref}
        rows={rows}
        className={classes}
        aria-invalid={invalid || undefined}
        {...rest}
      />
    );
  },
);
