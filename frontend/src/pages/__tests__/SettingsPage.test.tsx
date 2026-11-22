import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const deployment = vi.hoisted(() => ({ singleUser: false }));
vi.mock('../../hooks/useDeployment', () => ({ useDeployment: () => deployment }));
vi.mock('../../features/settings/store', () => ({ useSettings: () => [{}, vi.fn()] }));
vi.mock('../../hooks/usePublishPageContext', () => ({ usePublishPageContext: vi.fn() }));
vi.mock('../../host/registry', () => ({ getRegisteredSettingsSections: () => [] }));
vi.mock('../../components/ui', () => ({ PageShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>, PageSection: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock('../../features/settings/sections/ProfileSection', () => ({ ProfileSection: () => <h2>Profile details</h2> }));
vi.mock('../../features/settings/sections/AppearanceSection', () => ({ AppearanceSection: () => <h2>Appearance details</h2> }));
vi.mock('../../features/settings/sections/AboutSection', () => ({ AboutSection: () => <h2>About Integral</h2> }));
vi.mock('../../features/settings/sections/GetStartedSection', () => ({ GetStartedSection: () => <h2>Getting started</h2> }));
vi.mock('../../features/settings/sections/ConnectorsSection', () => ({ ConnectorsSection: () => <h2>Connector settings</h2> }));
vi.mock('../../features/settings/sections/AgentProfilesSection', () => ({ AgentProfilesSection: () => <h2>Agent profiles settings</h2> }));
vi.mock('../../features/settings/sections/AgentsSection', () => ({ AgentsSection: () => <h2>External agents settings</h2> }));
vi.mock('../../features/settings/sections/SkillsSection', () => ({ SkillsSection: () => <h2>Agent skills settings</h2> }));
vi.mock('../../features/settings/sections/EmailSection', () => ({ EmailSection: () => <h2>Email settings</h2> }));
vi.mock('../../features/settings/sections/WhatsAppSection', () => ({ WhatsAppSection: () => <h2>WhatsApp settings</h2> }));
vi.mock('../../features/settings/sections/AiModelsSection', () => ({ AiModelsSection: ({ initialCategory }: { initialCategory?: string }) => <h2>{initialCategory === 'embeddings' ? 'Embedding model settings' : initialCategory === 'speech-to-text' ? 'Speech to Text settings' : initialCategory === 'realtime-voice' ? 'Realtime Voice settings' : 'AI Model settings'}</h2> }));
import { SettingsPage } from '../SettingsPage';

afterEach(() => {
  cleanup();
  deployment.singleUser = false;
});
const show = (path = '/settings') => render(<MemoryRouter initialEntries={[path]}><SettingsPage /></MemoryRouter>);
describe('Settings navigation', () => {
  it('starts at Profile and groups the menu by purpose', async () => {
    show();
    expect(await screen.findByRole('heading', { name: 'Profile details' })).toBeInTheDocument();
    expect(screen.getByText('Your account')).toBeInTheDocument();
    expect(screen.getByText('AI & connections')).toBeInTheDocument();
    const ai = within(screen.getByRole('group', { name: 'AI & connections' }));
    expect(ai.queryByRole('group')).not.toBeInTheDocument();
    expect(ai.queryByText(/^(Agent Setup|Integrations)$/)).not.toBeInTheDocument();
    expect(ai.getAllByRole('button').map(button => button.textContent)).toEqual([
      'AI models', 'Agent Skills', 'Agent profiles', 'External Agents', 'Connectors',
    ]);
    expect(screen.getByText('Governance')).toBeInTheDocument();
    const workspace = screen.getByText('Workspace', { exact: true }).parentElement!;
    expect(within(workspace).getByRole('button', { name: 'Email' })).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: 'Settings sections' })).getAllByRole('button').map(b => b.textContent)).toEqual(['Profile', 'Appearance', 'Notifications', 'AI models', 'Agent Skills', 'Agent profiles', 'External Agents', 'Connectors', 'Search', 'Sharing', 'Email', 'Policies', 'Conflicts', 'Audit log', 'Help & about']);
  });
  it.each([
    ['email', 'Email settings', 'Email'],
    ['whatsapp', 'WhatsApp settings', 'Connectors'],
    ['voice-input', 'Speech to Text settings', 'AI models'],
    ['embedding-models', 'Embedding model settings', 'AI models'],
    ['realtime-voice', 'Realtime Voice settings', 'AI models'],
    ['channels', 'Connector settings', 'Connectors'],
    ['in-app-interfaces', 'Agent profiles settings', 'Agent profiles'],
    ['agents', 'External agents settings', 'External Agents'],
    ['skills', 'Agent skills settings', 'Agent Skills'],
  ])('preserves the #%s settings link', async (hash, heading, parent) => {
    show(`/settings#${hash}`);
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: parent })).toHaveAttribute('aria-current', 'page');
  });
  it('preserves the hidden Sharing destination in single-user mode', async () => {
    deployment.singleUser = true;
    show('/settings#sharing');
    expect(await screen.findByRole('heading', { name: 'Profile details' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sharing' })).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'AI & connections' })).toBeInTheDocument();
  });
  it('does not expose Operational Models through an old Settings link', async () => {
    show('/settings#library');
    expect(await screen.findByRole('heading', { name: 'Profile details' })).toBeInTheDocument();
    expect(screen.queryByText('Operational Models')).not.toBeInTheDocument();
  });
  it.each(['about', 'get-started'])('preserves the old #%s help link', async hash => {
    show(`/settings#${hash}`);
    expect(await screen.findByRole('heading', { name: 'Getting started' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'About Integral' })).toBeInTheDocument();
  });
  it('filters settings and shows an empty result explicitly', () => {
    show();
    fireEvent.change(screen.getByRole('textbox', { name: 'Find a setting' }), { target: { value: 'voice' } });
    expect(screen.getByRole('button', { name: 'Speech to Text' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Realtime Voice' })).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'AI & connections' })).getAllByRole('button')
      .map(button => button.textContent)).toEqual(['Speech to Text', 'Realtime Voice']);
    expect(screen.queryByRole('button', { name: 'Policies' })).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: 'Find a setting' }), { target: { value: 'nonexistent' } });
    expect(screen.getByText('No matching settings.')).toBeInTheDocument();
  });
  it('keeps the compact menu inside a short viewport as it resizes', () => {
    show();
    const trigger = screen.getByRole('button', { name: /Settings menu/ });
    const originalHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', { value: 350, configurable: true });
    const bounds = vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ top: 250, bottom: 294 } as DOMRect);
    try {
      fireEvent.click(trigger);
      const menu = document.getElementById('settings-compact-nav')!;
      expect(menu.style.bottom).toBe('calc(100% + 0.5rem)');
      expect(menu.style.getPropertyValue('--settings-menu-space')).toBe('234px');
      bounds.mockReturnValue({ top: 80, bottom: 124 } as DOMRect);
      Object.defineProperty(window, 'innerHeight', { value: 300, configurable: true });
      fireEvent(window, new Event('resize'));
      expect(menu.style.top).toBe('calc(100% + 0.5rem)');
      expect(menu.style.getPropertyValue('--settings-menu-space')).toBe('160px');
    } finally {
      Object.defineProperty(window, 'innerHeight', { value: originalHeight, configurable: true });
      bounds.mockRestore();
    }
  });

  it('focuses compact search and restores focus on Escape', () => {
    show();
    const trigger = screen.getByRole('button', { name: /Settings menu/ });
    fireEvent.click(trigger);
    const menu = document.getElementById('settings-compact-nav')!;
    expect(within(menu).getByRole('textbox')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });
});
