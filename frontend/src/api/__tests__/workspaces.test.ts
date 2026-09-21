import { describe, it, expect, vi } from 'vitest';
import { listWorkspaceOperationalModels, createWorkspaceFromOperationalModel } from '../workspaces';
import * as client from '../client';

describe('workspace profile client', () => {
  it('lists workspace profiles', async () => {
    const spy = vi.spyOn(client.default, 'get').mockResolvedValue({
      data: { profiles: [{ slug: 'crm-pm', name: 'CRM', description: '', tags: [] }] },
    });
    const r = await listWorkspaceOperationalModels();
    expect(r).toHaveLength(1);
    expect(r[0].slug).toBe('crm-pm');
    spy.mockRestore();
  });

  it('posts create with workspace_type and operational_model_slug', async () => {
    const spy = vi.spyOn(client.default, 'post').mockResolvedValue({
      data: {
        workspace: { id: 'ws1', kind: 'organization', name: 'Acme' },
      },
    });
    const created = await createWorkspaceFromOperationalModel({
      name: 'Acme',
      kind: 'organization',
      operationalModelSlug: 'crm-pm',
    });
    expect(spy).toHaveBeenCalledWith('/workspaces', {
      name: 'Acme',
      workspace_type: 'company',
      operational_model_slug: 'crm-pm',
    });
    expect(created.id).toBe('ws1');
    spy.mockRestore();
  });

  it('maps personal kind to workspace_type personal', async () => {
    const spy = vi.spyOn(client.default, 'post').mockResolvedValue({
      data: {
        workspace: { id: 'ws2', kind: 'personal', name: 'Side project' },
      },
    });
    const created = await createWorkspaceFromOperationalModel({
      name: 'Side project',
      kind: 'personal',
    });
    expect(spy).toHaveBeenCalledWith('/workspaces', {
      name: 'Side project',
      workspace_type: 'personal',
    });
    expect(created.id).toBe('ws2');
    spy.mockRestore();
  });
});
