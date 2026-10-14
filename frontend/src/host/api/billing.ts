import apiClient from '../../api/client';
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
  checkout: async (
    workspaceId: string,
    planKey: string,
  ): Promise<{ url: string }> => {
    const { data } = await apiClient.post<{ url: string }>('/billing/checkout', {
      workspace_id: workspaceId,
      plan_key: planKey,
    });
    return data;
  },
  checkoutCreditPack: async (
    workspaceId: string,
    packKey: string,
  ): Promise<{ url: string }> => {
    const { data } = await apiClient.post<{ url: string }>(
      '/billing/credit-packs/checkout',
      {
        workspace_id: workspaceId,
        pack_key: packKey,
      },
    );
    return data;
  },
  changePlan: async (
    workspaceId: string,
    planKey: string,
  ): Promise<{ status?: string; message?: string; url?: string }> => {
    const { data } = await apiClient.post('/billing/change-plan', {
      workspace_id: workspaceId,
      plan_key: planKey,
    });
    return data;
  },
  portal: async (workspaceId: string): Promise<{ url: string }> => {
    const { data } = await apiClient.post<{ url: string }>('/billing/portal', {
      workspace_id: workspaceId,
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
  getAiUsage: async (workspaceId: string): Promise<AiUsageSnapshot> => {
    const { data } = await apiClient.get<{ usage: AiUsageSnapshot }>(
      '/billing/ai-usage',
      {
        params: { workspace_id: workspaceId },
        __suppressSystemNotify: true,
      } as never,
    );
    return data.usage;
  },
};

export interface AiUsageSnapshot {
  workspace_id?: string;
  used: number;
  limit: number;
  remaining: number | null;
  weekly_remaining?: number | null;
  purchased_remaining?: number;
  effective_remaining?: number | null;
  percent: number;
  is_unlimited: boolean;
  exempt_used?: number;
  agent_key_source?: 'platform' | 'byok' | 'unavailable';
  is_soft_warning: boolean;
  is_exhausted: boolean;
  weekly_exhausted?: boolean;
  is_using_purchased?: boolean;
  window_days: number;
  enforcement_enabled: boolean;
  soft_warn_threshold?: number;
  meter?: string;
  unit?: string;
}
