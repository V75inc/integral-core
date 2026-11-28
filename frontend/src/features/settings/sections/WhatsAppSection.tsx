import { Link } from 'react-router-dom';

import { SettingsPageHeader } from '../components/SettingsPageHeader';

/** Legacy deep link. The number and the line are configured on the connector. */
export function WhatsAppSection() {
  return (
    <div id="whatsapp-settings" className="flex scroll-mt-20 flex-col gap-4">
      <Link to="/settings#connectors" className="self-start text-sm text-[var(--link)] underline">Back to Connectors</Link>
      <SettingsPageHeader
        title="WhatsApp"
        description="Link your number, and turn the line on or off, from the WhatsApp connector."
      />
    </div>
  );
}
