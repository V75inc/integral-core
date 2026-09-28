import apiClient from './client';
import type {
  BillingCatalog,
  BillingStatus,
  HostedSubscription,
  HostedSubscriptionList,
  HostedSubscriptionUpsert,
} from '../components/apps/billingAccess';

export type {
  BillingCatalog,
  BillingStatus,
  HostedSubscription,
  HostedSubscriptionList,
  HostedSubscriptionUpsert,
};

export const billingApi = {
  status: async (workspaceId: string): Promise<BillingStatus | null> => {
    try {
      return await billingApi.getStatus(workspaceId);
    } catch {
      return null;
    }
  },
  catalog: async (workspaceId: string): Promise<BillingCatalog | null> => {
    try {
      return await billingApi.getCatalog(workspaceId);
    } catch {
      return null;
    }
  },
  getStatus: async (workspaceId: string): Promise<BillingStatus> => {
    const { data } = await apiClient.get<BillingStatus>('/billing/status', {
      params: { workspace_id: workspaceId },
      __suppressSystemNotify: true,
    } as never);
    return data;
  },
  getCatalog: async (workspaceId: string): Promise<BillingCatalog> => {
    const { data } = await apiClient.get<BillingCatalog>('/billing/catalog', {
      params: { workspace_id: workspaceId },
      __suppressSystemNotify: true,
    } as never);
    return data;
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
  listSubscriptions: async (filters?: {
    status?: string;
    source?: string;
  }): Promise<HostedSubscriptionList> => {
    const { data } = await apiClient.get<HostedSubscriptionList>(
      '/billing/subscriptions',
      {
        params: {
          status: filters?.status || undefined,
          source: filters?.source || undefined,
        },
      },
    );
    return data ?? { subscriptions: [], total: 0 };
  },
  setSubscription: async (
    body: HostedSubscriptionUpsert,
  ): Promise<HostedSubscription> => {
    const { data } = await apiClient.post<HostedSubscription>(
      '/billing/subscription',
      body,
    );
    return data;
  },
  reconcile: async (): Promise<{ reconciled: boolean }> => {
    const { data } = await apiClient.post<{ reconciled: boolean }>(
      '/billing/reconcile',
    );
    return data ?? { reconciled: true };
  },
};
