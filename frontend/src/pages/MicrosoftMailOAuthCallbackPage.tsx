/**
 * Popup landing for the Microsoft 365 mail catalog connector.
 * Entra redirects here with ``?code&state``. The page posts those to the
 * authed callback, then ``postMessage``s the opener so the install sheet
 * can close.
 */

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { agentiveErrorMessage } from '../api/helpers';
import {
  MCP_OAUTH_MESSAGE_TYPE,
  connectorsApi,
  type MicrosoftMailCallbackResponse,
} from '../api/connectors';
import { Logo, LogoMark } from '../components/ui';
import { Surface, Text, Stack } from '../ui';

type Phase = 'working' | 'done' | 'error';

const inflightExchanges = new Map<string, Promise<MicrosoftMailCallbackResponse>>();

export function resetMicrosoftMailOAuthExchangesForTests() {
  inflightExchanges.clear();
}

function exchangeOnce(
  code: string,
  state: string,
): Promise<MicrosoftMailCallbackResponse> {
  const key = `${code}:${state}`;
  const existing = inflightExchanges.get(key);
  if (existing) return existing;
  const request = connectorsApi
    .microsoftMailCallback({ code, state })
    .catch(err => {
      inflightExchanges.delete(key);
      throw err;
    });
  inflightExchanges.set(key, request);
  return request;
}

function notifyOpener(payload: { ok: boolean; error?: string }) {
  const message = { type: MCP_OAUTH_MESSAGE_TYPE, ...payload };
  if (window.opener && !window.opener.closed) {
    window.opener.postMessage(message, window.location.origin);
  }
}

function closePopup() {
  window.close();
  window.setTimeout(() => {
    window.close();
  }, 250);
}

export function MicrosoftMailOAuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const [phase, setPhase] = useState<Phase>('working');
  const [error, setError] = useState('');

  useEffect(() => {
    const oauthError = searchParams.get('error');
    const description = searchParams.get('error_description');
    const code = searchParams.get('code');
    const state = searchParams.get('state');

    if (oauthError) {
      const message = description || oauthError;
      setError(message);
      setPhase('error');
      notifyOpener({ ok: false, error: message });
      closePopup();
      return;
    }
    if (!code || !state) {
      const message = 'Missing authorization code or state';
      setError(message);
      setPhase('error');
      notifyOpener({ ok: false, error: message });
      return;
    }

    let cancelled = false;
    exchangeOnce(code, state)
      .then(() => {
        if (cancelled) return;
        setPhase('done');
        notifyOpener({ ok: true });
        closePopup();
      })
      .catch(err => {
        if (cancelled) return;
        const message = agentiveErrorMessage(err, 'OAuth callback failed');
        setError(message);
        setPhase('error');
        notifyOpener({ ok: false, error: message });
      });
    return () => {
      cancelled = true;
    };
  }, [searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-4 py-10 sm:px-6 sm:py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <Logo to="/" size="md" />
        </div>
        {phase === 'working' ? (
          <div
            role="status"
            aria-label="Finishing Microsoft 365 authorization"
            className="flex items-center justify-center py-16"
            data-testid="ms-mail-oauth-callback-working"
          >
            <span className="animate-pulse">
              <LogoMark size="lg" />
            </span>
          </div>
        ) : (
          <Surface
            tone="panel"
            border="default"
            radius="card"
            elevation="card"
            padding="lg"
            role={phase === 'error' ? 'alert' : 'status'}
            data-testid={
              phase === 'error'
                ? 'ms-mail-oauth-callback-error'
                : 'ms-mail-oauth-callback-done'
            }
          >
            <Stack gap="sm" align="center">
              <Text variant="heading-sm" tone={phase === 'error' ? 'danger' : undefined}>
                {phase === 'error'
                  ? 'Authorization failed'
                  : 'Connected — you can close this window'}
              </Text>
              {error ? (
                <Text variant="body-sm" tone="muted" className="break-words text-center">
                  {error}
                </Text>
              ) : (
                <Text variant="body-sm" tone="muted" className="text-center">
                  Return to the settings tab to finish setup.
                </Text>
              )}
            </Stack>
          </Surface>
        )}
      </div>
    </div>
  );
}
