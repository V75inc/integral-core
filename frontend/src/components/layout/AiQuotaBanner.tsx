/**
 * AiQuotaBanner — system notification when platform-key AI credits are exhausted.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useScope } from '../../context/ScopeContext';
import { workspacesApi } from '../../api';
import { useSystemNotifications } from '../system';

const NOTIF_ID = 'billing:ai-quota-exhausted';
const dismissedKey = (workspaceId: string) =>
  `${NOTIF_ID}:dismissed:${workspaceId}`;

function wasDeferred(workspaceId: string): boolean {
  try {
    return sessionStorage.getItem(dismissedKey(workspaceId)) === '1';
  } catch {
    return false;
  }
}

export function AiQuotaBanner() {
  const { user } = useAuth();
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId;
  const navigate = useNavigate();
  const { notify, dismiss } = useSystemNotifications();
  const exhaustedRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!user || !workspaceId) {
      exhaustedRef.current = false;
      dismiss(NOTIF_ID);
      return;
    }
    if (wasDeferred(workspaceId)) {
      dismiss(NOTIF_ID);
      return;
    }
    try {
      const usage = await workspacesApi.getAiUsage(workspaceId);
      const exhausted =
        Boolean(usage.enforcement_enabled) &&
        !usage.is_unlimited &&
        Boolean(usage.is_exhausted);
      exhaustedRef.current = exhausted;
      if (!exhausted) {
        dismiss(NOTIF_ID);
        return;
      }
      const handleLater = () => {
        try {
          sessionStorage.setItem(dismissedKey(workspaceId), '1');
        } catch {
          /* session storage may be disabled */
        }
        dismiss(NOTIF_ID);
      };
      notify({
        id: NOTIF_ID,
        type: 'warning',
        icon: Sparkles,
        title: 'AI credits used up',
        body:
          'Platform AI credits for this workspace are exhausted for the rolling weekly window. ' +
          'Upgrade your plan, add your own model API key, or wait for older usage to roll off.',
        actions: [
          {
            label: 'Upgrade plan',
            onClick: () => navigate('/settings#billing'),
          },
          {
            label: 'Add API key',
            onClick: () => navigate('/settings#agents'),
          },
          { label: 'Not now', onClick: handleLater },
        ],
        dismissible: false,
      });
    } catch {
      if (!exhaustedRef.current) dismiss(NOTIF_ID);
    }
  }, [user, workspaceId, notify, dismiss, navigate]);

  useEffect(() => {
    void refresh();
    if (!user || !workspaceId) return;
    const id = window.setInterval(() => void refresh(), 60_000);
    return () => {
      window.clearInterval(id);
      dismiss(NOTIF_ID);
    };
  }, [user, workspaceId, refresh, dismiss]);

  return null;
}
