/**
 * HR onboarding contract public-share API (Business host — not Core sharing.ts).
 */

function getApiBase(): string {
  const env = import.meta.env.VITE_BACKEND_URL as string | undefined;
  if (env && env.trim()) {
    return env.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return 'http://localhost:4000';
}

export type OnboardingContractMeta = {
  update_available: boolean;
  generated_template_version_id?: string | null;
  published_template_version_id?: string | null;
  template_id?: string | null;
};

export const onboardingContractPublicShareApi = {
  getContractUrl(token: string, entryId: string): string {
    return `${getApiBase()}/api/public-share/track/${encodeURIComponent(token)}/entries/${encodeURIComponent(entryId)}/contract`;
  },

  async fetchContractBlob(
    token: string,
    entryId: string,
    cacheBust?: string | number,
  ): Promise<Blob> {
    const base = this.getContractUrl(token, entryId);
    const url =
      cacheBust != null && cacheBust !== ''
        ? `${base}?v=${encodeURIComponent(String(cacheBust))}`
        : base;
    const resp = await fetch(url, { cache: 'no-store' });
    if (resp.status !== 200) {
      const errData = await resp.json().catch(() => ({}));
      const detail = (errData as { detail?: unknown }).detail;
      const message = (errData as { message?: unknown }).message;
      const detailText =
        typeof detail === 'string'
          ? detail
          : typeof message === 'string'
            ? message
            : '';
      throw new Error(detailText || 'Failed to load contract PDF');
    }
    return resp.blob();
  },

  async fetchContractMeta(
    token: string,
    entryId: string,
  ): Promise<OnboardingContractMeta> {
    const resp = await fetch(
      `${getApiBase()}/api/public-share/track/${encodeURIComponent(token)}/entries/${encodeURIComponent(entryId)}/contract/meta`,
      { cache: 'no-store' },
    );
    if (resp.status !== 200) {
      const errData = await resp.json().catch(() => ({}));
      const detail = (errData as { detail?: unknown }).detail;
      const message = (errData as { message?: unknown }).message;
      const detailText =
        typeof detail === 'string'
          ? detail
          : typeof message === 'string'
            ? message
            : '';
      throw new Error(detailText || 'Failed to load contract status');
    }
    return resp.json();
  },

  async generateContract(
    token: string,
    entryId: string,
    options?: { force?: boolean },
  ): Promise<{
    contract_status?: string;
    reused?: boolean;
    update_available?: boolean;
  }> {
    const forceQs = options?.force === true ? '?force=1' : '';
    const resp = await fetch(
      `${getApiBase()}/api/public-share/track/${encodeURIComponent(token)}/entries/${encodeURIComponent(entryId)}/contract/generate${forceQs}`,
      { method: 'POST' },
    );
    if (resp.status !== 200) {
      const errData = await resp.json().catch(() => ({}));
      const detail = (errData as { detail?: unknown }).detail;
      const message = (errData as { message?: unknown }).message;
      const detailText =
        typeof detail === 'string'
          ? detail
          : typeof message === 'string'
            ? message
            : '';
      throw new Error(detailText || 'Failed to generate contract');
    }
    return resp.json();
  },

  async decideContract(
    token: string,
    entryId: string,
    body: { action: 'accept' | 'reject'; signature_png?: string; reason?: string },
  ): Promise<{ contract_status?: string }> {
    const resp = await fetch(
      `${getApiBase()}/api/public-share/track/${encodeURIComponent(token)}/entries/${encodeURIComponent(entryId)}/contract/decision`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    if (resp.status !== 200) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(
        (errData as { message?: string }).message ||
          (errData as { detail?: string }).detail ||
          'Failed to submit contract decision',
      );
    }
    return resp.json();
  },
};
