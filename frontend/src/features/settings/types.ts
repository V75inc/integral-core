/**
 * Settings domain types.
 *
 * Backend persistence lands later (initiative: settings-backend). For now
 * the settings store is FE-only via ``localStorage``, so the shapes here
 * are the single source of truth for the UI.
 */

/** Active AI harness routing. Surfaced as a radio toggle on the Agents
 *  panel. Exactly one value is current at a time. */
export type HarnessProviderId =
  /** Default. In-process jvagent embedded in the Integral backend. */
  | 'jvagent-embedded'
  /** Mock echo provider — for layout / theming work without a live agent. */
  | 'mock-echo';

export interface ProvidersSettings {
  /** Active harness routing. Default ``jvagent-embedded``. */
  defaultProviderId: HarnessProviderId;
}

export interface AppearanceSettings {
  /** Reserved — theme already lives in ThemeContext, mirrored here for the
   *  settings UI; switching here updates the context too. */
  theme: 'light' | 'dark';
}

/** Global retrieval (search) mode applied to every search surface. */
export type RetrievalModeSetting = 'graph' | 'semantic' | 'hybrid';

export interface RetrievalSettings {
  mode: RetrievalModeSetting;
}

export interface SettingsSnapshot {
  providers: ProvidersSettings;
  appearance: AppearanceSettings;
  retrieval: RetrievalSettings;
  /** Version to drive future migrations of the localStorage payload. */
  schemaVersion: number;
}

export const DEFAULT_SETTINGS: SettingsSnapshot = {
  schemaVersion: 1,
  providers: {
    defaultProviderId: 'jvagent-embedded',
  },
  appearance: {
    theme: 'light',
  },
  retrieval: {
    mode: 'semantic',
  },
};

export const RETRIEVAL_MODE_LABELS: Record<RetrievalModeSetting, string> = {
  semantic: 'Semantic (default)',
  hybrid: 'Hybrid',
  graph: 'Graph (in-memory)',
};
