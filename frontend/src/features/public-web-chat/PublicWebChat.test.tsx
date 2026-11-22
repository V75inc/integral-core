import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { PublicWebChat, type PublicWebChatTransport } from './PublicWebChat';

function transportFixture() {
  return {
    start: vi.fn<PublicWebChatTransport['start']>().mockResolvedValue({ kind: 'ready', messages: [] }),
    send: vi.fn<PublicWebChatTransport['send']>(),
  };
}

it('opens the full page, accepts keyboard send, and renders visitor and assistant text literally', async () => {
  const transport = transportFixture();
  const unsafeMarkup = '<img src=x onerror="throw 1">\nnext line';
  transport.send.mockImplementation(async ({ text }) => ({ kind: 'ready', messages: [
    { id: 'visitor', role: 'visitor', text },
    { id: 'assistant', role: 'assistant', text: unsafeMarkup },
  ] }));
  render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  expect(screen.getByRole('status')).toHaveTextContent('Opening chat');
  const input = await screen.findByRole('textbox', { name: 'Message' });
  expect(screen.getByRole('heading', { name: 'Ask Cedar', level: 1 })).toBeVisible();
  expect(transport.start).toHaveBeenCalledWith({ mode: 'resume', signal: expect.any(AbortSignal) });
  fireEvent.change(input, { target: { value: 'Please explain' } });
  fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  expect(transport.send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText('Please explain', { selector: 'p' });
  expect(screen.getByRole('log', { name: 'Conversation' })).toHaveTextContent('<img src=x');
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(input).toHaveValue('');
});

it('locks a pending attempt and reuses its original identity and text even after ambiguous failure', async () => {
  const transport = transportFixture();
  let finish!: (reply: Awaited<ReturnType<PublicWebChatTransport['send']>>) => void;
  transport.send
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockRejectedValueOnce(new Error('private details must not appear'))
    .mockResolvedValueOnce({ kind: 'ready', messages: [{ id: 'reply', role: 'assistant', text: 'One result.' }] });
  render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  const input = await screen.findByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: 'Hello there' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(screen.getByRole('status')).toHaveTextContent('Waiting for a reply');
  expect(input).toHaveAttribute('readonly');
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(transport.send).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ kind: 'pending' }); });
  fireEvent.click(screen.getByRole('button', { name: 'Check for reply' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm delivery');
  expect(screen.queryByText(/private details/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry message' }));
  expect(await screen.findByText('One result.')).toBeVisible();
  const [first] = transport.send.mock.calls[0];
  expect(first.id).toBeTruthy();
  expect(transport.send.mock.calls.map(([{ id, text }]) => [id, text])).toEqual([
    [first.id, 'Hello there'], [first.id, 'Hello there'], [first.id, 'Hello there'],
  ]);
});

it('clears expired history, never resends to a new session, and retries the same start mode', async () => {
  const transport = transportFixture();
  transport.start
    .mockResolvedValueOnce({ kind: 'ready', messages: [{ id: 'old', role: 'assistant', text: 'Old session.' }] })
    .mockRejectedValueOnce(new Error('Unavailable'))
    .mockResolvedValueOnce({ kind: 'ready', messages: [] });
  transport.send.mockResolvedValue({ kind: 'expired' });
  render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  const input = await screen.findByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: 'Must not resend' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  const restart = await screen.findByRole('button', { name: 'Start a new chat' });
  expect(screen.queryByText('Old session.')).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.click(restart);
  fireEvent.click(await screen.findByRole('button', { name: 'Retry opening chat' }));
  expect(await screen.findByRole('textbox', { name: 'Message' })).toHaveValue('');
  expect(transport.start.mock.calls.map(([{ mode }]) => mode)).toEqual(['resume', 'new', 'new']);
  expect(transport.send).toHaveBeenCalledTimes(1);
});

it('offers a recoverable unavailable state without a composer', async () => {
  const transport = transportFixture();
  transport.start.mockResolvedValueOnce({ kind: 'unavailable' });
  render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  const retry = await screen.findByRole('button', { name: 'Check again' });
  expect(screen.getByRole('status')).toHaveTextContent('This chat is unavailable');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.click(retry);
  expect(await screen.findByRole('textbox')).toBeEnabled();
});

it('aborts a replaced transport and ignores a stale response', async () => {
  const previous = transportFixture();
  const next = transportFixture();
  let finish!: (reply: Awaited<ReturnType<PublicWebChatTransport['start']>>) => void;
  previous.start.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<PublicWebChat title="Old chat" transport={previous} />);
  await waitFor(() => expect(previous.start).toHaveBeenCalledTimes(1));
  view.rerender(<PublicWebChat title="New chat" transport={next} />);
  expect(previous.start.mock.calls[0][0].signal.aborted).toBe(true);
  await act(async () => { finish({ kind: 'ready', messages: [{ id: 'old', role: 'assistant', text: 'Stale result' }] }); });
  expect(await screen.findByRole('textbox')).toBeEnabled();
  await waitFor(() => expect(screen.queryByText('Stale result')).not.toBeInTheDocument());
});


it('rejects oversized input and aborts an outstanding send on unmount', async () => {
  const transport = transportFixture();
  transport.send.mockImplementation(() => new Promise(() => {}));
  const view = render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  const input = await screen.findByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: 'x'.repeat(1001) } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(transport.send).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: 'Valid message' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  const [{ signal }] = transport.send.mock.calls[0];
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
});
