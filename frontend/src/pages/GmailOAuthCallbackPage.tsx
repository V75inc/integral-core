import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { connectorsApi, MCP_OAUTH_MESSAGE_TYPE } from '../api/connectors';
import { Text } from '../ui';

export default function GmailOAuthCallbackPage() {
  const [params] = useSearchParams();
  const exchanged = useRef(false);
  const [status, setStatus] = useState('Completing Gmail authorization…');

  useEffect(() => {
    const code = params.get('code');
    const state = params.get('state');
    if (!code || !state || params.has('error')) {
      setStatus('Gmail authorization was not completed.');
      return;
    }
    if (exchanged.current) return;
    exchanged.current = true;
    void connectorsApi.gmailOauthCallback({ code, state }).then(() => {
      setStatus('Gmail authorization completed. You can close this window.');
      window.opener?.postMessage({ type: MCP_OAUTH_MESSAGE_TYPE, ok: true }, window.location.origin);
      window.close();
    }, () => setStatus('Gmail authorization failed. Return to Settings and try again.'));
  }, [params]);

  return <Text as="p" role="status">{status}</Text>;
}
