/**
 * A non-409 HTTP failure used to render the raw error envelope — a JSON blob
 * with `error_code`, `timestamp`, `path` — as the assistant's reply. The 409
 * branch already parsed `{message}`; every other status now does too, and
 * falls back to a human sentence carrying the status code.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../../api/session', () => ({
  getAccessToken: vi.fn(() => 'access-1'),
  refreshAccessToken: vi.fn(),
}));
vi.mock('../../../../api/client', () => ({
  getActiveScopeHeader: () => null,
}));
vi.mock('../../../../api/aiChat', () => ({ aiChatApi: {} }));
vi.mock('../../../../config', () => ({ getApiBaseURL: () => 'http://api.test' }));

import { JvAgentProvider } from '../JvAgentProvider';

function ctx() {
  return {
    threadId: 'thread-1',
    userMessageText: 'hello',
    abortSignal: new AbortController().signal,
  } as never;
}

async function drain(iter: AsyncIterable<unknown>): Promise<unknown[]> {
  const out: unknown[] = [];
  for await (const ev of iter) out.push(ev);
  return out;
}

describe('JvAgentProvider.streamTurn non-409 failures', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("says the server's own message when the envelope carries one", async () => {
    const envelope = {
      error_code: 'model_quota',
      message: 'The model quota for this workspace is exhausted.',
      details: {},
      timestamp: '2026-09-08T00:00:00Z',
      path: '/api/chat/threads/thread-1/messages',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify(envelope), { status: 500 })),
    );

    const events = (await drain(JvAgentProvider.streamTurn(ctx()))) as Array<{
      type: string;
      code?: string;
      message?: string;
    }>;

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('error');
    expect(events[0].code).toBe('http_500');
    expect(events[0].message).toBe('The model quota for this workspace is exhausted.');
    expect(events[0].message).not.toContain('error_code');
  });

  it('falls back to a human sentence with the status when the body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 })),
    );

    const events = (await drain(JvAgentProvider.streamTurn(ctx()))) as Array<{
      message?: string;
    }>;

    expect(events[0].message).toContain('HTTP 502');
    expect(events[0].message).not.toContain('<html>');
  });

  it('does not treat an envelope without a message as a message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error_code: 'boom' }), { status: 500 }),
      ),
    );

    const events = (await drain(JvAgentProvider.streamTurn(ctx()))) as Array<{
      message?: string;
    }>;

    expect(events[0].message).toContain('HTTP 500');
    expect(events[0].message).not.toContain('{');
  });
});
