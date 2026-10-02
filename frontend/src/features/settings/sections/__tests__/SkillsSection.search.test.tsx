import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsSection } from '../SkillsSection';

const { listSkills, listTools } = vi.hoisted(() => ({
  listSkills: vi.fn(),
  listTools: vi.fn(),
}));

vi.mock('../../../../api/skills', () => ({
  skillsApi: {
    list: listSkills,
    toolCatalogue: listTools,
    update: vi.fn(),
  },
}));

vi.mock('../../../../context/ScopeContext', () => ({
  useScope: () => ({
    scope: { workspaceId: 'workspace-1' },
    activeWorkspace: { id: 'workspace-1', your_role: 'admin' },
    isPersonal: false,
  }),
}));

vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

vi.mock('../../../../components/skills/SkillEditorModal', () => ({
  SkillEditorModal: () => null,
  skillBadges: () => [],
}));

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SkillsSection />
    </QueryClientProvider>,
  );
}

describe('AI Skills search', () => {
  beforeEach(() => {
    listSkills.mockResolvedValue({
      total: 2,
      workspace: [],
      apps: [],
      core: [
        {
          id: 'skill-workspace',
          source: 'core',
          name: 'Workspace',
          description: 'Manage workspace context',
          key: 'workspace',
          kind: 'prompt',
          read_only: true,
          enabled: true,
          customized: false,
          stale_default: false,
          tools_required: [],
        },
        {
          id: 'skill-filing',
          source: 'core',
          name: 'Filing',
          description: 'Organize documents',
          key: 'filing',
          kind: 'prompt',
          read_only: true,
          enabled: true,
          customized: false,
          stale_default: false,
          tools_required: [],
        },
      ],
    });
    listTools.mockResolvedValue({
      total: 2,
      tools: [
        {
          name: 'integral_workspace_setup',
          friendly_label: 'Workspace setup',
          description: 'Prepare a workspace',
          param_summary: '',
        },
        {
          name: 'integral_query',
          friendly_label: 'Query records',
          description: 'Search records',
          param_summary: '',
        },
      ],
    });
  });

  it('filters skills and tools together as the user types', async () => {
    renderSection();
    const search = await screen.findByRole('textbox', { name: 'Search skills and tools' });
    fireEvent.change(search, { target: { value: 'Workspace' } });

    expect(await screen.findByText('integral_workspace_setup')).toBeInTheDocument();
    expect(await screen.findByText('Workspace setup')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('Filing')).not.toBeInTheDocument();
      expect(screen.queryByText('Query records')).not.toBeInTheDocument();
    });
  });

  it('shows a no-match message when neither skills nor tools match', async () => {
    renderSection();
    const search = await screen.findByRole('textbox', { name: 'Search skills and tools' });
    fireEvent.change(search, { target: { value: 'missing-capability' } });

    expect(await screen.findByText('No skills match “missing-capability”')).toBeInTheDocument();
  });
});
