import { useCallback, useEffect, useRef } from 'react';
import apiClient from '../api/client';
import {
  connectDesktopEnvironment,
  getDesktopEnvironmentConfig,
  onDesktopEnvironmentDisconnected,
  type DesktopEnvironmentSession,
} from '../config';
import { getSystemNotificationsApi } from '../components/system';

const NOTIFICATION_ID = 'desktop:environment-connection';

interface DesktopEnvironmentStatus {
  enabled: boolean;
  selector_present: boolean;
  binding_live: boolean;
  reason: string;
  live_bindings_for_scope: number;
  capabilities: string[];
}

/**
 * Binds the authenticated desktop main process to the active workspace.
 *
 * Browsers have no preload bridge, so this is a no-op there. Each reconnect
 * mints a fresh single-use ticket; long-lived user JWTs never enter Electron
 * main or a WebSocket query string.
 */
export function useDesktopEnvironment(workspaceId?: string): void {
  const connecting = useRef(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(async () => {
    const config = getDesktopEnvironmentConfig();
    if (
      !workspaceId ||
      !config?.enabled ||
      !config.deviceId ||
      connecting.current
    ) {
      return;
    }
    connecting.current = true;
    try {
      const response = await apiClient.post<DesktopEnvironmentSession>(
        '/agentive/desktop-environments/session',
        {
          device_id: config.deviceId,
          device_name: config.deviceName || 'Integral Desktop',
        },
        {
          __suppressSystemNotify: true,
          headers: { 'X-Integral-Scope': `ws:${workspaceId}` },
        } as never,
      );
      const connected = await connectDesktopEnvironment(response.data);
      if (connected) {
        const status = await apiClient.get<DesktopEnvironmentStatus>(
          '/agentive/desktop-environments/status',
          {
            headers: { 'X-Integral-Scope': `ws:${workspaceId}` },
            __suppressSystemNotify: true,
          } as never,
        );
        if (status.data.binding_live) {
          getSystemNotificationsApi()?.dismiss(NOTIFICATION_ID);
        } else {
          getSystemNotificationsApi()?.notify({
            id: NOTIFICATION_ID,
            type: 'warning',
            title: 'Desktop environment binding rejected',
            body: `Backend reason: ${status.data.reason}.`,
            dismissible: true,
          });
        }
      }
    } catch (error) {
      const status =
        typeof error === 'object' &&
        error !== null &&
        'response' in error &&
        typeof error.response === 'object' &&
        error.response !== null &&
        'status' in error.response
          ? Number(error.response.status)
          : null;
      getSystemNotificationsApi()?.notify({
        id: NOTIFICATION_ID,
        type: 'warning',
        title: 'Desktop environment unavailable',
        body:
          status === 503
            ? 'The backend could not register the desktop application environment.'
            : 'Integral could not connect its local environment host. Check the backend and retry.',
        dismissible: true,
      });
    } finally {
      connecting.current = false;
    }
  }, [workspaceId]);

  useEffect(() => {
    void connect();
    const unsubscribe = onDesktopEnvironmentDisconnected(() => {
      getSystemNotificationsApi()?.notify({
        id: NOTIFICATION_ID,
        type: 'warning',
        title: 'Desktop environment disconnected',
        body: 'Reconnecting the local environment host…',
        dismissible: true,
      });
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      reconnectTimer.current = setTimeout(() => void connect(), 1_000);
    });
    return () => {
      unsubscribe();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect]);
}
