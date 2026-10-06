/**
 * Shared sizing for sandboxed extension_view iframes (e.g. Email Log mail log panel).
 * Uses explicit height so the iframe is not stuck at the browser default (~150px).
 */
export const EXTENSION_VIEW_SHELL_CLASS =
  'relative w-full h-[calc(100dvh-var(--app-topbar-height)-var(--system-bar-h,0px)-11rem)] min-h-[32rem]';

export const EXTENSION_VIEW_IFRAME_CLASS =
  'w-full h-full min-h-[32rem] border border-[var(--panel-border)] rounded-[var(--radius-card)] bg-[var(--bg)]';

export const EXTENSION_VIEW_LOADING_CLASS =
  'h-[calc(100dvh-var(--app-topbar-height)-var(--system-bar-h,0px)-11rem)] min-h-[32rem] rounded-[var(--radius-card)] bg-[var(--panel-2)] animate-pulse';

/** Document Templates TipTap editor — full track width + tall viewport (matches integral SPA editor). */
export const EXTENSION_VIEW_TEMPLATE_EDITOR_SHELL_CLASS =
  'relative w-[calc(100%+1.5rem)] max-w-none -mx-3 md:w-[calc(100%+8rem)] md:-mx-16 ' +
  'h-[calc(100dvh-var(--app-topbar-height)-var(--system-bar-h,0px)-9rem)] min-h-[36rem]';

export const EXTENSION_VIEW_TEMPLATE_EDITOR_IFRAME_CLASS =
  'w-full h-full min-h-[36rem] border-0 rounded-none bg-[var(--bg)]';

export const EXTENSION_VIEW_TEMPLATE_EDITOR_MIN_HEIGHT = 720;
