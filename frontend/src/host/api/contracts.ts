import apiClient from '../../api/client';

/** Business-owned contract routes; Core exposes only the generic host slot. */
function path(token: string, entryId: string): string {
  return `/public-share/track/${encodeURIComponent(token)}/entries/${encodeURIComponent(entryId)}/contract`;
}

export type OnboardingContractMeta = {
  update_available: boolean;
  generated_template_version_id?: string | null;
  published_template_version_id?: string | null;
  template_id?: string | null;
};

/** Business-owned member contract extension; keep domain routes out of Core. */
export const memberContractApi = {
  async fetchContractMeta(entryId: string): Promise<OnboardingContractMeta> {
    const response = await apiClient.get('/me/assigned-form/contract/meta', {
      params: { entry: entryId },
    });
    return response.data as OnboardingContractMeta;
  },
};

export const publicContractApi = {
  async fetchOnboardingContractMeta(
    token: string,
    entryId: string,
  ): Promise<OnboardingContractMeta> {
    const response = await apiClient.get(`${path(token, entryId)}/meta`);
    return response.data as OnboardingContractMeta;
  },

  async fetchOnboardingContractBlob(token: string, entryId: string, cacheBust?: string | number): Promise<Blob> {
    const response = await apiClient.get(path(token, entryId), {
      responseType: 'blob', params: cacheBust == null ? undefined : { v: cacheBust },
    });
    return response.data as Blob;
  },
  async generateOnboardingContract(token: string, entryId: string, options?: { force?: boolean }): Promise<unknown> {
    const response = await apiClient.post(`${path(token, entryId)}/generate`, undefined, {
      params: options?.force ? { force: '1' } : undefined,
    });
    return response.data;
  },
  async decideOnboardingContract(token: string, entryId: string, body: {
    action: 'accept' | 'reject'; signature_png?: string; reason?: string;
  }): Promise<unknown> {
    const response = await apiClient.post(`${path(token, entryId)}/decision`, body);
    return response.data;
  },
};
