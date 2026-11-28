import type { ReactNode } from 'react';

import { PageMeta } from '../../../components/ui/PageMeta';
import { Surface, Text } from '../../../ui';
import { SETTINGS_PANEL_BORDER } from './settingsPanelBorder';

/** A page title on the content background, separate from settings panels. */
interface HeaderProps {
  title: ReactNode;
  description: ReactNode;
  actions?: ReactNode;
}

export function SettingsPageHeader({ title, description, actions }: HeaderProps) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <Text variant="heading-md" weight="semibold" as="h2">{title}</Text>
        <PageMeta as="p">{description}</PageMeta>
      </div>
      {actions}
    </header>
  );
}

/** For a settings page with a single content panel below its title. */
export function SettingsPagePanel({ children, outlined = false, ...header }: HeaderProps & {
  children: ReactNode;
  outlined?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <SettingsPageHeader {...header} />
      <Surface border={outlined ? "none" : "subtle"} radius="card" padding="lg"
        className={outlined ? SETTINGS_PANEL_BORDER : undefined}>
        <div className="flex flex-col gap-4">{children}</div>
      </Surface>
    </div>
  );
}
