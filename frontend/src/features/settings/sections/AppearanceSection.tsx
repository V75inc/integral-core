import { useTheme } from '../../../context/ThemeContext';
import { SettingsField, SettingsSection } from '../components/Field';
import { Text } from '../../../ui';
import type { SettingsSnapshot } from '../types';

interface Props {
  settings: SettingsSnapshot;
  update: (
    next: SettingsSnapshot | ((prev: SettingsSnapshot) => SettingsSnapshot),
  ) => void;
  // Accepted for SettingsPage render-ctx spread compatibility; this
  // section doesn't use it today.
  navigateToSection?: (id: string) => void;
}

// `update` is accepted so the existing `<SettingsPage>` render contract
// stays uniform across sections; the body just doesn't drive a mutation
// path yet. Density / compact-mode controls were removed alongside the
// dead provider-config sweep; reintroduce once a global density consumer
// ships.
export function AppearanceSection(_props: Props) {
  const { theme, setTheme } = useTheme();

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Appearance
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Choose how Integral looks across the workspace.
        </Text>
      </div>

      <SettingsSection
        title="Theme"
        description="Switch between light and dark."
      >
        <SettingsField label="Color theme" inline>
          <div className="flex gap-1 rounded-[var(--radius-pill)] border border-[var(--panel-border)] bg-[var(--panel-2)] p-1">
            {(['light', 'dark'] as const).map(t => (
              <button
                key={t}
                type="button"
                onClick={() => setTheme(t)}
                className={`
                  rounded-[var(--radius-pill)] px-3 py-1 text-xs capitalize
                  transition-colors duration-fast
                  ${theme === t ? 'bg-[var(--panel)] text-[var(--text)]' : 'text-[var(--text-subtle)] hover:text-[var(--text-muted)]'}
                `}
              >
                {t}
              </button>
            ))}
          </div>
        </SettingsField>
      </SettingsSection>
    </div>
  );
}
