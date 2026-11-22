import { Link } from 'react-router-dom';

import { SettingsPageHeader } from '../components/SettingsPageHeader';
import { WhatsAppConnectorSettings } from '../connectors/WhatsAppConnectorSettings';

/** Legacy deep link, including the notification verification shortcut. */
export function WhatsAppSection() {
  return (
    <div id="whatsapp-settings" className="flex scroll-mt-20 flex-col gap-4">
      <Link to="/settings#connector-library" className="self-start text-sm text-[var(--link)] underline">Back to Connector Library</Link>
      <SettingsPageHeader title="WhatsApp" description="Manage your workspace line and link your personal number." />
      <WhatsAppConnectorSettings />
    </div>
  );
}
