import { useQueryClient } from '@tanstack/react-query';

import { NOTIFICATION_PREFS_KEY } from '../../../api/notificationPreferences';
import { WhatsAppSettings, type WhatsAppDialog } from '../../../components/settings/WhatsAppSettings';
import { useScopeOptional } from '../../../context/ScopeContext';

export function WhatsAppConnectorSettings({ dialog }: { dialog?: WhatsAppDialog }) {
  const qc = useQueryClient();
  const scope = useScopeOptional();
  const role = scope?.activeWorkspace?.your_role;
  return (
    <WhatsAppSettings
      key={scope?.scope?.workspaceId}
      dialog={dialog}
      canManageLine={role === 'owner' || role === 'admin'}
      onBindingChange={() => void qc.invalidateQueries({ queryKey: ['connectors'] })}
      onChange={() => void qc.invalidateQueries({ queryKey: NOTIFICATION_PREFS_KEY })}
    />
  );
}
