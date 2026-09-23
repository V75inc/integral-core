/**
 * Popup landing for native Gmail OAuth (mirror + mail tools). Google
 * redirects here with ``?code&state`` (or ``?error``). We POST the code to
 * the authed backend, then ``postMessage`` the result back to the opener on
 * the shared install-sheet channel and close.
 *
 * Route is authed (RequireAuth) and outside Layout so the popup is a
 * bare status screen. Every terminal branch closes the popup: a stuck
 * popup leaves the install sheet waiting forever.
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { agentiveErrorMessage } from "../api/helpers";
import {
  MCP_OAUTH_MESSAGE_TYPE,
  connectorsApi,
  type GmailOAuthCallbackResponse,
} from "../api/connectors";
import { Logo, LogoMark } from "../components/ui";
import { Surface, Text, Stack } from "../ui";

type Phase = "working" | "done" | "error";

const inflightExchanges = new Map<
  string,
  Promise<GmailOAuthCallbackResponse>
>();

export function resetGmailOAuthExchangesForTests() {
  inflightExchanges.clear();
}

function exchangeOnce(code: string, state: string) {
  const key = `${code}:${state}`;
  const existing = inflightExchanges.get(key);
  if (existing) return existing;
  const request = connectorsApi
    .gmailOauthCallback({ code, state })
    .catch((err) => {
      inflightExchanges.delete(key);
      throw err;
    });
  inflightExchanges.set(key, request);
  return request;
}

function notifyOpener(payload: {
  ok: boolean;
  connector_id?: string;
  connection_mode?: string;
  error?: string;
}) {
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

export function GmailOAuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const [phase, setPhase] = useState<Phase>("working");
  const [error, setError] = useState("");

  useEffect(() => {
    const oauthError = searchParams.get("error");
    const description = searchParams.get("error_description");
    const code = searchParams.get("code");
    const state = searchParams.get("state");

    if (oauthError) {
      const message = description || oauthError;
      setError(message);
      setPhase("error");
      notifyOpener({ ok: false, error: message });
      closePopup();
      return;
    }
    if (!code || !state) {
      const message = "Missing authorization code or state";
      setError(message);
      setPhase("error");
      notifyOpener({ ok: false, error: message });
      closePopup();
      return;
    }

    let cancelled = false;
    exchangeOnce(code, state)
      .then((cb) => {
        if (cancelled) return;
        setPhase("done");
        notifyOpener({
          ok: true,
          connector_id: cb.connector_id,
          connection_mode: cb.connection_mode,
        });
        closePopup();
      })
      .catch((err) => {
        if (cancelled) return;
        const message = agentiveErrorMessage(err, "OAuth callback failed");
        setError(message);
        setPhase("error");
        notifyOpener({ ok: false, error: message });
        closePopup();
      });
    return () => {
      cancelled = true;
    };
  }, [searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-6 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <Logo to="/" size="md" />
        </div>
        {phase === "working" ? (
          <div
            role="status"
            aria-label="Finishing Gmail authorization"
            className="flex items-center justify-center py-16"
            data-testid="gmail-oauth-callback-working"
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
            role={phase === "error" ? "alert" : "status"}
            data-testid={
              phase === "error"
                ? "gmail-oauth-callback-error"
                : "gmail-oauth-callback-done"
            }
          >
            <Stack gap="sm" align="center">
              <Text
                variant="heading-sm"
                tone={phase === "error" ? "danger" : undefined}
              >
                {phase === "error"
                  ? "Authorization failed"
                  : "Connected — you can close this window"}
              </Text>
              {error ? (
                <Text variant="body-sm" tone="muted" className="text-center">
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
