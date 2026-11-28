import type { ConfirmOptions } from '../../../context/ConfirmContext';

export function connectorRemoval(name: string, workspaceGrant = false): ConfirmOptions {
  return {
    title: `Remove ${name}?`,
    message: workspaceGrant
      ? 'This removes this workspace’s grant and disconnects its integration with the shared line. Other workspaces keep their grants.'
      : 'This removes all configuration for this connector installation and disconnects its integrations. Other installations and external accounts are unaffected.',
    cancelLabel: 'Cancel',
    confirmLabel: 'Remove connector',
    variant: 'danger',
  };
}
