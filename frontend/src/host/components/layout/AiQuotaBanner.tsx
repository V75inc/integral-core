/**
 * AiQuotaBanner — system notification when platform-key AI credits are exhausted.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { useScope } from '../../../context/ScopeContext';
import { billingApi } from '../../api/billing';
import { useSystemNotifications } from '../../../components/system';

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
  const refreshGeneration = useRef(0);
  const queryClient = useQueryClient();

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
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
      const usage = await billingApi.getAiUsage(workspaceId);
      if (generation !== refreshGeneration.current) return;
      const exhausted =
        Boolean(usage.enforcement_enabled) &&
        !usage.is_unlimited &&
        Boolean(usage.is_exhausted) &&
        usage.agent_key_source !== 'byok';
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
          'Your weekly allowance and purchased balance are spent. Top up to keep using platform AI.',
        actions: [
          {
            label: 'Top up balance',
            onClick: () => navigate('/settings#billing-credit-packs'),
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
      if (generation !== refreshGeneration.current) return;
      if (!exhaustedRef.current) dismiss(NOTIF_ID);
    }
  }, [user, workspaceId, notify, dismiss, navigate]);

  useEffect(() => {
    void refresh();
    if (!user || !workspaceId) return;
    const id = window.setInterval(() => void refresh(), 60_000);
    const unsubscribe = queryClient.getQueryCache().subscribe(event => {
      if (event.type === 'updated' && event.action.type === 'success'
        && event.query.queryKey[0] === 'model-credentials') {
        void refresh();
      }
    });
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => {
      ++refreshGeneration.current;
      window.clearInterval(id);
      unsubscribe();
      window.removeEventListener('focus', onFocus);
      dismiss(NOTIF_ID);
    };
  }, [user, workspaceId, refresh, dismiss, queryClient]);

  return null;
}
