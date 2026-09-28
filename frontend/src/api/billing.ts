import apiClient from './client';
import type { BillingCatalog, BillingStatus } from '../components/apps/billingAccess';

export const billingApi = {
  status: async (workspaceId: string): Promise<BillingStatus | null> => {
    try {
      const { data } = await apiClient.get<BillingStatus>(
        '/billing/status',
        {
          params: { workspace_id: workspaceId },
          __suppressSystemNotify: true,
        } as never,
      );
      return data ?? null;
    } catch {
      return null;
    }
  },
  catalog: async (workspaceId: string): Promise<BillingCatalog | null> => {
    try {
      const { data } = await apiClient.get<BillingCatalog>(
        '/billing/catalog',
        {
          params: { workspace_id: workspaceId },
          __suppressSystemNotify: true,
        } as never,
      );
      return data ?? null;
    } catch {
      return null;
    }
  },
  checkout: async (workspaceId: string): Promise<{ url: string }> => {
    const { data } = await apiClient.post<{ url: string }>('/billing/checkout', {
      workspace_id: workspaceId,
    });
    return data;
  },
  portal: async (workspaceId: string): Promise<{ url: string }> => {
    const { data } = await apiClient.post<{ url: string }>('/billing/portal', {
      workspace_id: workspaceId,
    });
    return data;
  },
  addAddon: async (
    workspaceId: string,
    slug: string,
  ): Promise<{ status?: string; message?: string; url?: string }> => {
    const { data } = await apiClient.post('/billing/addons', {
      workspace_id: workspaceId,
      slug,
    });
    return data;
  },
};
