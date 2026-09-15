import apiClient from './client';
import { unwrapResource } from './helpers';
import type { Invitation, InvitationStatus, WorkspaceRole, WorkspaceSummary } from '../types';

export interface CreateInvitationBody {
  email: string;
  role: Exclude<WorkspaceRole, 'owner'>;
  can_create_apps?: boolean;
  can_create_tracks?: boolean;
  message?: string;
}

export interface CreateInvitationResponse {
  invitation: Invitation;
  acceptance_url: string;
}

export interface InvitationPreview {
  invitation: Invitation;
  workspace: WorkspaceSummary | null;
  error_code?: string | null;
}

export const invitationsApi = {
  /** Owner/admin issues a new invitation. Returns the invitation + acceptance URL. */
  create: async (
    workspaceId: string,
    body: CreateInvitationBody,
  ): Promise<CreateInvitationResponse> => {
    const { data } = await apiClient.post(
      `/workspaces/${workspaceId}/invitations`,
      body,
    );
    return data as CreateInvitationResponse;
  },

  /** List invitations for a workspace (any member may read). */
  list: async (
    workspaceId: string,
    status?: InvitationStatus,
  ): Promise<Invitation[]> => {
    const params = status ? { status } : undefined;
    const { data } = await apiClient.get(
      `/workspaces/${workspaceId}/invitations`,
      { params },
    );
    const list = (data as { invitations?: Invitation[] })?.invitations;
    return Array.isArray(list) ? list : [];
  },

  /** Revoke an invitation (owner/admin or original inviter). */
  revoke: async (
    workspaceId: string,
    invitationId: string,
  ): Promise<Invitation> => {
    const { data } = await apiClient.delete(
      `/workspaces/${workspaceId}/invitations/${invitationId}`,
    );
    return unwrapResource<Invitation>(data, 'invitation');
  },

  /** Revoke a resource-level (App/Track/Entry) invitation. Caller must own
   *  the invitation's target resource. Phase 8 Plan 08-05 (B2) — DO NOT use
   *  for workspace-targeted invitations; use `revoke(workspaceId, invitationId)`
   *  instead (this endpoint refuses workspace invitations with 400). */
  revokeAny: async (invitationId: string): Promise<Invitation> => {
    const { data } = await apiClient.delete(`/invitations/${invitationId}`);
    return unwrapResource<Invitation>(data, 'invitation');
  },

  /** Unauthenticated preview of an invitation by plaintext token. */
  preview: async (token: string): Promise<InvitationPreview> => {
    const { data } = await apiClient.get(`/invitations/${token}`);
    return data as InvitationPreview;
  },

  /** Authenticated accept — materialises IS_MEMBER_OF for the calling user. */
  accept: async (token: string): Promise<{ invitation: Invitation; role: string }> => {
    const { data } = await apiClient.post(`/invitations/${token}/accept`);
    return data as { invitation: Invitation; role: string };
  },

  /** Decline — token alone is sufficient auth for this. */
  decline: async (token: string): Promise<Invitation> => {
    const { data } = await apiClient.post(`/invitations/${token}/decline`);
    return unwrapResource<Invitation>(data, 'invitation');
  },
};
