import apiClient from './client';

export type WorkItemStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_human'
  | 'waiting_for_event'
  | 'retry_wait'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired'
  | 'dead_letter';

/** Safe projection of lifecycle work authorized for the current principal. */
export interface WorkItemStatusResponse {
  work_item_id: string;
  kind: string;
  status: WorkItemStatus;
  workspace_id: string;
  app_id: string;
  attempt: number;
  next_attempt_at: string;
  updated_at: string;
  result_refs: string[];
  failure?: { code: string; message?: string };
}

export const workItemsApi = {
  get: (workItemId: string) =>
    apiClient
      .get<WorkItemStatusResponse>(`/work-items/${workItemId}`)
      .then(response => response.data),
};
