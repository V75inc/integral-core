/**
 * Popup callback for Microsoft 365 mail — posts code/state and notifies
 * the opener via postMessage.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';

const callbackMock = vi.fn();

vi.mock('../api/connectors', () => ({
  MCP_OAUTH_MESSAGE_TYPE: 'integral:mcp-oauth',
  connectorsApi: {
    microsoftMailCallback: (...args: unknown[]) => callbackMock(...args),
  },
}));

import {
  MicrosoftMailOAuthCallbackPage,
  resetMicrosoftMailOAuthExchangesForTests,
} from './MicrosoftMailOAuthCallbackPage';

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/connectors/microsoft-mail/callback${search}`]}>
      <MicrosoftMailOAuthCallbackPage />
    </MemoryRouter>,
  );
}

describe('MicrosoftMailOAuthCallbackPage', () => {
  let closeSpy: ReturnType<typeof vi.fn>;
  let postMessageSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    callbackMock.mockReset();
    resetMicrosoftMailOAuthExchangesForTests();
    closeSpy = vi.fn();
    postMessageSpy = vi.fn();
    Object.defineProperty(window, 'close', {
      configurable: true,
      value: closeSpy,
    });
    Object.defineProperty(window, 'opener', {
      configurable: true,
      value: { closed: false, postMessage: postMessageSpy },
    });
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('exchanges code and state, notifies the opener, and closes', async () => {
    callbackMock.mockResolvedValue({
      connector_id: 'con-1',
      connected: true,
      reauth_required: false,
    });
    renderAt('?code=abc&state=signed');
    await waitFor(() =>
      expect(callbackMock).toHaveBeenCalledWith({
        code: 'abc',
        state: 'signed',
      }),
    );
    await waitFor(() => expect(postMessageSpy).toHaveBeenCalled());
    expect(postMessageSpy.mock.calls[0][0]).toEqual({
      type: 'integral:mcp-oauth',
      ok: true,
    });
    expect(closeSpy).toHaveBeenCalled();
  });

  it('reports a missing code without calling the API', async () => {
    renderAt('?state=signed');
    expect(await screen.findByTestId('ms-mail-oauth-callback-error')).toBeInTheDocument();
    expect(callbackMock).not.toHaveBeenCalled();
    expect(postMessageSpy.mock.calls[0][0]).toMatchObject({
      type: 'integral:mcp-oauth',
      ok: false,
    });
  });
});
