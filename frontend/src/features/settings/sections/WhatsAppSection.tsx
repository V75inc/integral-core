import { useQueryClient } from '@tanstack/react-query';

import { NOTIFICATION_PREFS_KEY } from '../../../api/notificationPreferences';
import { WhatsAppSettings } from '../../../components/settings/WhatsAppSettings';

/** Workspace settings for the personal WhatsApp channel. */
export function WhatsAppSection() {
  const qc = useQueryClient();
  return (
    <div id="whatsapp-settings" className="scroll-mt-20">
      <WhatsAppSettings
        onChange={() => void qc.invalidateQueries({ queryKey: NOTIFICATION_PREFS_KEY })}
      />
    </div>
  );
}
