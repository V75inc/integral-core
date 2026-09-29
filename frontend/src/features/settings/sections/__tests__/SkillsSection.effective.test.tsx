import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { listMock, effectiveMock, updateMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  effectiveMock: vi.fn(),
  updateMock: vi.fn(),
}));

let activeSkillEnabled = true;

vi.mock('../../../../api/skills', () => ({
  skillsApi: {
    list: listMock,
    effective: effectiveMock,
    update: updateMock,
  },
}));

vi.mock('../../../../context/ScopeContext', () => ({
  useScope: () => ({
    scope: { workspaceId: 'workspace-1' },
    activeWorkspace: { your_role: 'owner' },
    isPersonal: true,
  }),
}));

vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

vi.mock('../../../../components/skills/SkillEditorModal', () => ({
  SkillEditorModal: () => null,
  skillBadges: () => [],
}));

import { SkillsSection } from '../SkillsSection';

function renderSection() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SkillsSection />
    </QueryClientProvider>,
  );
}

describe('SkillsSection effective turn catalogue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    activeSkillEnabled = true;
    listMock.mockResolvedValue({
      core: [],
      apps: [],
      workspace: [
        {
          id: 'skill-mutable',
          source: 'workspace',
          read_only: false,
          key: 'mutable',
          name: 'Mutable skill',
          description: 'Can be paused',
          kind: 'skill',
          enabled: true,
          customized: false,
          stale_default: false,
          tools_required: [],
        },
      ],
      total: 1,
    });
    updateMock.mockImplementation(async (_id: string, body: { enabled?: boolean }) => {
      activeSkillEnabled = Boolean(body.enabled);
      return { enabled: activeSkillEnabled };
    });
    effectiveMock.mockImplementation(async () => ({
      workspace_id: 'workspace-1',
      focused_app_id: null,
      apps: [{ id: 'app-1', name: 'Operations' }],
      skills: [
        {
          id: 'skill-available',
          key: 'review',
          name: 'Review',
          description: 'Reviews a record',
          source: 'core',
          state: 'available',
          tools_required: ['integral_get_entry'],
        },
        {
          id: 'skill-paused',
          key: 'draft',
          name: 'Draft',
          description: 'Paused in this workspace',
          source: 'workspace',
          state: 'paused',
          reason: 'Disabled by workspace owner',
          tools_required: [],
        },
        {
          id: 'skill-mutable',
          key: 'mutable',
          name: 'Mutable skill',
          description: 'Can be paused',
          source: 'workspace',
          state: activeSkillEnabled ? 'available' : 'paused',
          reason: activeSkillEnabled ? null : 'Disabled by workspace owner',
          tools_required: [],
        },
        {
          id: 'skill-unavailable',
          key: 'locked',
          name: 'Locked',
          description: 'Not loaded for this turn',
          source: 'app',
          state: 'unavailable',
          tools_required: [],
        },
      ],
      tools: [
        {
          name: 'integral_get_entry',
          description: 'Read an Entry',
          source: 'core',
        },
      ],
    }));
  });

  it('shows runtime availability, tools, reasons, and refreshes when App focus changes', async () => {
    renderSection();

    expect(await screen.findByText('Skills and tools available here')).toBeInTheDocument();
    expect(screen.getAllByText('Available').length).toBeGreaterThan(0);
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('Not loaded')).toBeInTheDocument();
    expect(screen.getByText('Disabled by workspace owner')).toBeInTheDocument();
    expect(screen.getByText('integral_get_entry')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('App focus for available skills'), {
      target: { value: 'app-1' },
    });
    await waitFor(() => expect(effectiveMock).toHaveBeenLastCalledWith('app-1'));
  });

  it('refreshes the effective catalogue when a workspace skill is paused', async () => {
    renderSection();

    expect(await screen.findByText('Skills and tools available here')).toBeInTheDocument();
    expect(screen.getAllByText('Paused')).toHaveLength(1);

    fireEvent.click(screen.getByRole('switch', { name: 'Disable skill' }));

    await waitFor(() => {
      expect(updateMock).toHaveBeenCalledWith('skill-mutable', { enabled: false });
      expect(screen.getAllByText('Paused')).toHaveLength(2);
    });
  });
});
