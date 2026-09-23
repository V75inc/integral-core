/**
 * Surface — panel / card primitive.
 *
 * Renders a backgrounded, optionally bordered, optionally shadowed
 * container. Replaces the 35+ raw `bg-[var(--panel)] border
 * border-[var(--panel-border)] rounded-[var(--radius-card)]` literal
 * combinations found across feature code (see
 * `.planning/ui-templating/INVENTORY.md` § 4).
 *
 * Compose with `<Text>` and `<Stack>` (Phase 4) for the standard
 * panel-with-heading pattern.
 *
 * See:
 * - `.planning/ui-templating/adr-frontend-layering.md` — primitive rules.
 * - `.planning/ui-templating/TOKENS.md` § Surface + § Radii + § Shadows.
 *
 * **Forbidden:** passing arbitrary Tailwind classes via `className` for
 * size, spacing, color, or radius. Use `tone`, `border`, `radius`,
 * `elevation`, or `padding` instead. `className` is reserved for layout
 * positioning (grid placement, alignment, flex sizing).
 */

import { type ElementType, type ReactNode } from 'react';

/** Background color tier. */
export type SurfaceTone =
  | 'panel' // --panel (default card surface)
  | 'panel-2' // --panel-2 (inset / secondary surface)
  | 'transparent'; // no background (border / radius only)

/** Border style + color. */
export type SurfaceBorder =
  | 'default' // --panel-border (default hairline)
  | 'subtle' // --border-subtle (quieter divider color)
  | 'none';

/** Corner radius. */
export type SurfaceRadius =
  | 'card' // --radius-card (14px — cards, panels, dialogs)
  | 'input' // --radius-input (8px — inputs, buttons)
  | 'pill' // --radius-pill (9999px)
  | 'none';

/** Drop shadow. */
export type SurfaceElevation =
  | 'flat' // no shadow
  | 'sm' // --shadow-sm (hover / badges)
  | 'card' // --shadow-card (cards, popovers)
  | 'pop'; // --shadow-pop (dialogs, command palette)

/** Padding preset. Use `'none'` for hand-rolled inner layouts. */
export type SurfacePadding = 'none' | 'sm' | 'md' | 'lg';

const TONE_CLASSES: Record<SurfaceTone, string> = {
  panel: 'bg-[var(--panel)]',
  'panel-2': 'bg-[var(--panel-2)]',
  transparent: '',
};

const BORDER_CLASSES: Record<SurfaceBorder, string> = {
  default: 'border border-[var(--panel-border)]',
  subtle: 'border border-[var(--border-subtle)]',
  none: '',
};

const RADIUS_CLASSES: Record<SurfaceRadius, string> = {
  card: 'rounded-[var(--radius-card)]',
  input: 'rounded-[var(--radius-input)]',
  pill: 'rounded-[var(--radius-pill)]',
  none: '',
};

const ELEVATION_CLASSES: Record<SurfaceElevation, string> = {
  flat: '',
  sm: 'shadow-[var(--shadow-sm)]',
  card: 'shadow-[var(--shadow-card)]',
  pop: 'shadow-[var(--shadow-pop)]',
};

const PADDING_CLASSES: Record<SurfacePadding, string> = {
  none: '',
  sm: 'p-2',
  md: 'p-3',
  lg: 'p-4 sm:p-5',
};

export interface SurfaceProps {
  children?: ReactNode;
  /** Background. Default: `panel`. */
  tone?: SurfaceTone;
  /** Border style. Default: `default`. */
  border?: SurfaceBorder;
  /** Corner radius. Default: `card`. */
  radius?: SurfaceRadius;
  /** Drop shadow. Default: `flat`. */
  elevation?: SurfaceElevation;
  /** Inner padding. Default: `none` — let the caller compose interior layout. */
  padding?: SurfacePadding;
  /** Render element. Default: `div`. */
  as?: ElementType;
  /**
   * Layout-positioning classes only — `flex`, `grid`, `min-w-0`, span,
   * alignment overrides. NEVER background, border, radius, shadow, or
   * padding utilities — those belong to the typed props above.
   */
  className?: string;
  /** HTML id (e.g. for `aria-labelledby`). */
  id?: string;
  /** ARIA role override. */
  role?: string;
  /** Test hook for a surface that is a meaningful feature boundary. */
  'data-testid'?: string;
}

export function Surface({
  children,
  tone = 'panel',
  border = 'default',
  radius = 'card',
  elevation = 'flat',
  padding = 'none',
  as,
  className,
  id,
  role,
  'data-testid': testId,
}: SurfaceProps) {
  const Element = (as ?? 'div') as ElementType;

  const classes = [
    TONE_CLASSES[tone],
    BORDER_CLASSES[border],
    RADIUS_CLASSES[radius],
    ELEVATION_CLASSES[elevation],
    PADDING_CLASSES[padding],
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <Element id={id} role={role} className={classes} data-testid={testId}>
      {children}
    </Element>
  );
}
