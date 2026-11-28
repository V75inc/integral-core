import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { PublicWebChat, type PublicWebChatTransport } from './PublicWebChat';

// jsdom doesn't implement browser layout or its scrolling/resize primitives.
const originalScrollTo = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTo');

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  });
  Object.defineProperty(Element.prototype, 'scrollTo', { value: vi.fn(), configurable: true });
});

afterEach(() => {
  if (originalScrollTo) Object.defineProperty(Element.prototype, 'scrollTo', originalScrollTo);
  else Reflect.deleteProperty(Element.prototype, 'scrollTo');
  vi.unstubAllGlobals();
});

function transportFixture() {
  return {
    start: vi.fn<PublicWebChatTransport['start']>().mockResolvedValue({ kind: 'ready', messages: [] }),
    send: vi.fn<PublicWebChatTransport['send']>(),
  };
}

it('opens the full page, accepts keyboard send, and renders safe assistant Markdown', async () => {
  const transport = transportFixture();
  const unsafeMarkup = '<img src=x onerror="throw 1">\n\n**Formatted reply** [Read more](https://example.invalid/) [Unsafe](javascript:alert%281%29) ![Image description](https://example.invalid/blocked.png)';
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
  expect(input.closest('form')).toBeNull();
  fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  expect(transport.send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText('Please explain');
  expect(await screen.findByText('Formatted reply', { selector: 'strong' })).toBeVisible();
  expect(await screen.findByRole('link', { name: 'Read more' })).toHaveAttribute('href', 'https://example.invalid/');
  expect(await screen.findByText('Image description')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
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
  expect(await screen.findByRole('status')).toHaveTextContent('Waiting for a reply');
  expect(screen.getAllByRole('status')).toHaveLength(1);
  expect(screen.getByRole('log', { name: 'Conversation' })).toHaveTextContent('Hello there');
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
  await waitFor(() => expect(transport.send).toHaveBeenCalledTimes(1));
  const [{ signal }] = transport.send.mock.calls[0];
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
});

it('does not append queued composer text from an old runtime on route change', async () => {
  const previous = transportFixture();
  const next = transportFixture();
  const view = render(<PublicWebChat title="Old chat" transport={previous} />);
  const input = await screen.findByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: 'Belongs to the old conversation' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  view.rerender(<PublicWebChat title="New chat" transport={next} />);
  expect(await screen.findByRole('textbox', { name: 'Message' })).toHaveValue('');
  expect(previous.send).not.toHaveBeenCalled();
  expect(next.send).not.toHaveBeenCalled();
});

it('offers a separately focusable history and leaves keyboard focus in the composer after send', async () => {
  const transport = transportFixture();
  transport.start.mockResolvedValue({
    kind: 'ready', messages: [{ id: 'initial', role: 'assistant', text: 'Earlier reply' }],
  });
  transport.send.mockResolvedValue({
    kind: 'ready', messages: [{ id: 'latest', role: 'assistant', text: 'Latest reply' }],
  });
  render(<PublicWebChat title="Ask Cedar" transport={transport} />);
  const input = await screen.findByRole('textbox', { name: 'Message' });
  const history = screen.getByRole('log', { name: 'Conversation' });
  expect(history).toHaveAttribute('tabindex', '0');
  expect(history).toHaveTextContent('Earlier reply');
  expect(history).not.toContainElement(input);
  input.focus();
  fireEvent.change(input, { target: { value: 'Another question' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(await screen.findByText('Latest reply')).toBeVisible();
  expect(input).toHaveFocus();
});
