import {useEffect, useState} from 'react';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, expect, it, vi} from 'vitest';
import {useAppUI} from '@integral/sdk-react';
import {AppReactComponentHost} from './AppReactComponentHost';
import api from '../../api/client';
import {loadReactArtifact} from './reactArtifactLoader';
const state = vi.hoisted(() => ({workspace: 'ws', principal: 'user', enabled: true}));
vi.mock('../../context/AuthContext', () => ({useAuth: () => ({user: {id: state.principal}})}));
vi.mock('../../context/ScopeContext', () => ({useScope: () => ({scope: {workspaceId: state.workspace}})}));
vi.mock('../../context/ToastContext', () => ({useToast: () => ({showToast: vi.fn()})}));
vi.mock('../../context/ConfirmContext', () => ({useConfirm: () => vi.fn().mockResolvedValue(true)}));
vi.mock('../../api/client', () => ({default: {post: vi.fn()}}));
vi.mock('../../hooks/useChangeEventInvalidation', () => ({CHANGE_EVENT_APPLIED: 'test-change'}));
vi.mock('./reactArtifactLoader', () => ({loadReactArtifact: vi.fn()}));
const digest = 'a'.repeat(64);
function Widget() {
  const ui = useAppUI();
  const [value, setValue] = useState('');
  return <><input aria-label="Draft" value={value} onChange={event => setValue(event.target.value)} /><button onClick={() => void ui.invoke('save', {}).catch(error => setValue(error.message))}>Submit</button><p>{ui.workspaceId}</p></>;
}
function shell(preview = false) {
  return <QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><MemoryRouter><AppReactComponentHost appId="app" componentKey="editor" placement="track" preview={preview} fallback={<p>Legacy view</p>} /></MemoryRouter></QueryClientProvider>;
}
beforeEach(() => {
  state.workspace = 'ws'; state.principal = 'user'; state.enabled = true;
  vi.mocked(loadReactArtifact).mockReset().mockResolvedValue({default: Widget});
  vi.mocked(api.post).mockReset().mockImplementation(async () => ({data: {enabled: state.enabled, artifact_url: `/api/ui-artifacts/sample/${digest}/index.mjs`, component: {digest, export_name: 'default', sdk_range: '^1.0.0'}}}));
});
it('mounts only an admitted component and preserves draft state across rerenders', async () => {
  const page = render(shell());
  const input = await screen.findByLabelText('Draft');
  fireEvent.change(input, {target: {value: 'Unsaved'}});
  page.rerender(shell());
  expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved');
  expect(loadReactArtifact).toHaveBeenCalledTimes(1);
});
it('uses the legacy view when the operator switch is disabled', async () => {
  state.enabled = false;
  render(shell());
  expect(await screen.findByText('Legacy view')).toBeInTheDocument();
  expect(loadReactArtifact).not.toHaveBeenCalled();
});
it('does not load code after a denied descriptor', async () => {
  vi.mocked(api.post).mockRejectedValue(new Error('Denied'));
  render(shell());
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
  expect(loadReactArtifact).not.toHaveBeenCalled();
});
it('blocks preview writes before HTTP dispatch', async () => {
  render(shell(true));
  fireEvent.click(await screen.findByText('Submit'));
  await waitFor(() => expect(screen.getByLabelText('Draft')).toHaveValue('Business actions are disabled.'));
  expect(vi.mocked(api.post).mock.calls.every(([url]) => String(url).endsWith('/mount'))).toBe(true);
});
it('replaces component state on a workspace or principal switch', async () => {
  const page = render(shell());
  fireEvent.change(await screen.findByLabelText('Draft'), {target: {value: 'Private draft'}});
  state.workspace = 'other';
  page.rerender(shell());
  await waitFor(() => expect(screen.getByLabelText('Draft')).toHaveValue(''));
  expect(screen.getByText('other')).toBeInTheDocument();
  const calls = vi.mocked(api.post).mock.calls;
  expect(calls.some(([, , config]) => (config?.headers as Record<string, string>)?.['X-Integral-Scope'] === 'ws:other')).toBe(true);
});
it('does not populate the next workspace with a delayed old query', async () => {
  let resolve!: (value: unknown) => void;
  function Reader() {
    const ui = useAppUI(); const [text, setText] = useState('waiting');
    useEffect(() => {
      const controller = new AbortController();
      ui.query('records', {}, controller.signal).then(value => setText(String(value.title))).catch(() => undefined);
      return () => controller.abort();
    }, [ui.query]);
    return <p>{text}</p>;
  }
  vi.mocked(loadReactArtifact).mockResolvedValue({default: Reader});
  const mount = vi.mocked(api.post).getMockImplementation()!;
  vi.mocked(api.post).mockImplementation((url, ...args) => String(url).endsWith('/queries/records') ? new Promise(done => {resolve = done;}) : mount(url, ...args));
  const page = render(shell());
  await screen.findByText('waiting');
  state.workspace = 'other'; page.rerender(shell());
  await act(async () => {resolve({data: {title: 'Old private data'}});});
  expect(screen.queryByText('Old private data')).not.toBeInTheDocument();
});
it('keeps the legacy draft mounted until an enabled native view is explicitly accepted', async () => {
  state.enabled = false;
  render(shell());
  const legacy = await screen.findByText('Legacy view');
  state.enabled = true;
  act(() => window.dispatchEvent(new Event('test-change')));
  await screen.findByText('A newer view is available.');
  expect(screen.getByText('Legacy view')).toBe(legacy);
  expect(loadReactArtifact).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Reload view'}));
  await screen.findByLabelText('Draft');
  expect(screen.queryByText('Legacy view')).not.toBeInTheDocument();
  expect(loadReactArtifact).toHaveBeenCalledTimes(1);
});

it('defers host-owned field data until the operator selects the fallback', async () => {
  let complete!: (value: any) => void;
  vi.mocked(api.post).mockImplementation(() => new Promise(resolve => {complete = resolve;}));
  const owner = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><AppReactComponentHost appId="app" componentKey="editor" placement="entry_compose" onFieldDataOwnerChange={owner} fallback={<p>Legacy form</p>} /></MemoryRouter></QueryClientProvider>);
  expect(owner).not.toHaveBeenCalled();
  await act(async () => {complete({data: {enabled: false, component: {digest, sdk_range: '^1.1.0'}}});});
  await screen.findByText('Legacy form');
  expect(owner).toHaveBeenLastCalledWith('host');
  expect(loadReactArtifact).not.toHaveBeenCalled();
});
