import { afterEach, describe, expect, it, vi } from 'vitest';
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('./index', () => ({ apiClient: client }));
import { aiChatApi } from './aiChat';
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});
describe('acknowledged thread cancellation', () => {
  it('waits for the observed turn to release admission', async () => {
    vi.useFakeTimers();
    client.post.mockResolvedValue({
      data: { thread_id: 'thread', cancelled: true, work_item_id: 'old', status: 'cancelled' },
    });
    client.get
      .mockResolvedValueOnce({ data: { running: true, work_item_id: 'old' } })
      .mockResolvedValueOnce({ data: { running: true, work_item_id: 'old' } })
      .mockResolvedValueOnce({ data: { running: false } });
    const done = vi.fn();
    const pending = aiChatApi.cancelThread('thread').then(done);
    await vi.advanceTimersByTimeAsync(499);
    expect(done).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(done).toHaveBeenCalledOnce();
  });
  it('does not wait for or cancel a newer turn', async () => {
    client.post.mockResolvedValue({
      data: { thread_id: 'thread', cancelled: true, turn_id: 'old' },
    });
    client.get
      .mockResolvedValueOnce({ data: { running: true, turn_id: 'old' } })
      .mockResolvedValue({ data: { running: true, turn_id: 'new' } });
    await aiChatApi.cancelThread('thread');
    expect(client.post).toHaveBeenCalledOnce();
    expect(client.get).toHaveBeenCalledTimes(2);
  });
  it('reports a bounded timeout instead of pretending work stopped', async () => {
    vi.useFakeTimers();
    client.post.mockResolvedValue({
      data: { thread_id: 'thread', cancelled: true, turn_id: 'old' },
    });
    client.get.mockResolvedValue({ data: { running: true, turn_id: 'old' } });
    const pending = expect(aiChatApi.cancelThread('thread')).rejects.toThrow(
      'Stopping is taking longer',
    );
    await vi.advanceTimersByTimeAsync(30_000);
    await pending;
  });
  it('propagates cancellation failure without polling for completion', async () => {
    client.get.mockResolvedValue({ data: { running: true, turn_id: 'old' } });
    client.post.mockRejectedValue(new Error('Unavailable'));
    await expect(aiChatApi.cancelThread('thread')).rejects.toThrow(
      'Unavailable',
    );
    expect(client.get).toHaveBeenCalledOnce();
  });
  it('retains the observed target for retries after a network failure', async () => {
    client.get.mockResolvedValue({
      data: { running: true, work_item_id: 'original' },
    });
    client.post.mockRejectedValueOnce(new Error('Unavailable'));
    const onTarget = vi.fn();
    await expect(
      aiChatApi.cancelThread('thread', null, onTarget),
    ).rejects.toThrow('Unavailable');
    const target = onTarget.mock.calls[0][0];
    client.post.mockResolvedValue({
      data: { thread_id: 'thread', cancelled: false },
    });
    await aiChatApi.cancelThread('thread', target);
    expect(client.post.mock.calls[1][1]).toEqual({
      expected_work_item_id: 'original',
      expected_turn_id: undefined,
    });
    expect(client.get).toHaveBeenCalledOnce();
  });
  it('returns no-active distinctly without sending cancellation or declaring work stopped', async () => {
    client.get.mockResolvedValue({ data: { running: false } });
    await expect(aiChatApi.cancelThread('thread')).resolves.toMatchObject({
      cancelled: false, outcome: 'no_active',
    });
    expect(client.post).not.toHaveBeenCalled();
  });
  it('returns not-confirmed for a refusal without touching a newer turn', async () => {
    client.get.mockResolvedValue({ data: { running: true, turn_id: 'old' } });
    client.post.mockResolvedValue({ data: { thread_id: 'thread', cancelled: false } });
    await expect(aiChatApi.cancelThread('thread')).resolves.toEqual({
      thread_id: 'thread', cancelled: false, outcome: 'not_confirmed',
    });
    expect(client.get).toHaveBeenCalledTimes(1);
    expect(client.post).toHaveBeenCalledTimes(1);
  });

  it('does not accept a successful-looking receipt for an unrelated observed target', async () => {
    client.get.mockResolvedValue({ data: { running: true, work_item_id: 'old' } });
    client.post.mockResolvedValue({ data: { thread_id: 'thread', cancelled: true, work_item_id: 'new', status: 'cancelled' } });
    await expect(aiChatApi.cancelThread('thread')).resolves.toMatchObject({ cancelled: false, outcome: 'not_confirmed' });
    expect(client.get).toHaveBeenCalledTimes(1);
  });

  it.each(['cancelled', 'succeeded', 'failed', 'expired', 'dead_letter'])(
    'checks exact durable outcome %s once a cancellation request releases admission',
    async status => {
      client.get
        .mockResolvedValueOnce({ data: { running: true, work_item_id: 'old', turn_id: 'old-turn' } })
        .mockResolvedValueOnce({ data: { running: true, work_item_id: 'new', turn_id: 'new-turn' } })
        .mockResolvedValueOnce({ data: { work_status: status } });
      client.post.mockResolvedValue({
        data: { thread_id: 'thread', cancelled: true, work_item_id: 'old', turn_id: 'old-turn', status: 'running' },
      });
      await expect(aiChatApi.cancelThread('thread')).resolves.toMatchObject({
        outcome: status === 'cancelled' ? 'cancelled' : 'settled',
        cancelled: status === 'cancelled',
        target: { work_item_id: 'old', turn_id: 'old-turn' },
      });
      expect(client.get).toHaveBeenNthCalledWith(3, '/chat/threads/thread/work-items/old/events',
        { params: { limit: 1 }, timeout: 5000 });
      expect(client.post).toHaveBeenCalledTimes(1);
    },
  );

  it('keeps waiting when admission looks idle but exact durable work is not terminal', async () => {
    vi.useFakeTimers();
    client.get
      .mockResolvedValueOnce({ data: { running: true, work_item_id: 'old' } })
      .mockResolvedValueOnce({ data: { running: false } })
      .mockResolvedValueOnce({ data: { work_status: 'running' } })
      .mockResolvedValueOnce({ data: { running: false } })
      .mockResolvedValueOnce({ data: { work_status: 'cancelled' } });
    client.post.mockResolvedValue({ data: { thread_id: 'thread', cancelled: true, work_item_id: 'old', status: 'running' } });
    const received = vi.fn();
    const pending = aiChatApi.cancelThread('thread').then(received);
    await vi.advanceTimersByTimeAsync(499);
    expect(received).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(received).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ outcome: 'cancelled' }));
  });

  it('refuses malformed activity instead of declaring completion', async () => {
    client.get.mockResolvedValue({ data: {} });
    await expect(aiChatApi.cancelThread('thread')).rejects.toThrow(
      'Could not confirm',
    );
    expect(client.post).not.toHaveBeenCalled();
  });
});

it('backs off polling while waiting for durable cancellation', async () => {
  vi.useFakeTimers();
  client.post.mockResolvedValue({
    data: { thread_id: 'thread', cancelled: true, work_item_id: 'old', status: 'cancelled' },
  });
  client.get.mockResolvedValue({
    data: { running: true, work_item_id: 'old' },
  });
  const pending = expect(aiChatApi.cancelThread('thread')).rejects.toThrow(
    'Stopping is taking longer',
  );
  await vi.advanceTimersByTimeAsync(30_000);
  await pending;
  // One target read plus at most 18 completion reads, including the deadline.
  expect(client.get.mock.calls.length).toBeLessThanOrEqual(19);
  expect(client.post).toHaveBeenCalledOnce();
});
