import { Link } from 'react-router-dom';

import { SettingsPageHeader } from '../components/SettingsPageHeader';

/** Legacy deep link. The line is configured on the package panel. */
export function WhatsAppSection() {
  return (
    <div id="whatsapp-settings" className="flex scroll-mt-20 flex-col gap-4">
      <Link to="/settings#connectors" className="self-start text-sm text-[var(--link)] underline">Back to Connectors</Link>
      <SettingsPageHeader title="WhatsApp" description="The workspace line is saved in Dropped-in packages on Connectors." />
    </div>
  );
}
