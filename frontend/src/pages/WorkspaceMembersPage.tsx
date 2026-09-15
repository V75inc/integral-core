import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Copy, Mail, Trash2 } from 'lucide-react';
import { workspacesApi, invitationsApi } from '../api';
import {
  filterWorkspaceInvitations,
  filterWorkspaceMembers
} from '../api/members';
import type { Workspace, WorkspaceMember } from '../api/workspaces';
import { UserSearchPicker } from '../components/collab/UserSearchPicker';
import {
  Avatar,
  AvatarStackedMeta,
  Badge,
  Button,
  Skeleton,
  LINE_ICON_STROKE,
  PageHeading,
  PageShell,
  PageSection,
  filterBar,
  SearchRow,
  PageSearchInput
} from '../components/ui';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { useToast } from '../context/ToastContext';
import { useSetCrumbs } from '../context/CrumbsContext';
import { isSamePrincipal } from '../utils';
import type {
  Invitation,
  WorkspaceRole,
  User
} from '../types';
import { usePublishPageContext } from '../hooks/usePublishPageContext';

const ASSIGNABLE_ROLES: ReadonlyArray<Exclude<WorkspaceRole, 'owner'>> = [
  'admin',
  'member',
  'guest',
];

function roleVariant(role: string): 'info' | 'warning' | 'default' {
  const r = String(role || '').toLowerCase();
  if (r === 'admin') return 'info';
  if (r === 'guest') return 'warning';
  return 'default';
}

function RoleBadge({ role }: { role: string }) {
  const r = String(role || '').toLowerCase();
  const label = role ? role.charAt(0).toUpperCase() + role.slice(1) : 'Member';
  // Owner renders as plain muted text — no chip background — so the
  // common case stays quiet. Other roles keep their chip variant for
  // visual distinction in mixed lists.
  if (r === 'owner') {
    return <span className="text-xs text-[var(--text-muted)]">{label}</span>;
  }
  return <Badge variant={roleVariant(role)}>{label}</Badge>;
}

function InviteStatusPill({ status }: { status: string }) {
  const s = String(status || '').toLowerCase();
  const v =
    s === 'pending' ? 'info'
    : s === 'accepted' ? 'success'
    : s === 'expired' ? 'warning'
    : s === 'declined' || s === 'revoked' ? 'danger'
    : 'default';
  return <Badge variant={v}>{s ? s.charAt(0).toUpperCase() + s.slice(1) : '—'}</Badge>;
}

export function WorkspaceMembersPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { user: me } = useAuth();
  const confirm = useConfirm();
  const { showToast } = useToast();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);

  // Counts and role mix only — a member roster is other people's names and
  // email addresses, and page context is forwarded with every turn.
  usePublishPageContext(
    workspaceId
      ? {
          pageKind: 'workspace_members',
          metadata: {
            workspace_id: workspaceId,
            member_count: members.length,
          },
        }
      : null,
  );
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  // Add-existing-user (search picker) role selection.
  const [pickerRole, setPickerRole] =
    useState<Exclude<WorkspaceRole, 'owner'>>('member');

  // Invite-by-email form state.
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] =
    useState<Exclude<WorkspaceRole, 'owner'>>('member');
  const [inviteMessage, setInviteMessage] = useState('');
  const [inviting, setInviting] = useState(false);
  const [lastAcceptanceUrl, setLastAcceptanceUrl] = useState<string | null>(null);

  const isPersonal = workspace?.kind === 'personal';

  const myRole = useMemo<WorkspaceRole | null>(() => {
    if (!me) return null;
    const mine = members.find(m => isSamePrincipal(me, m.user_id || m.id));
    const r = String(mine?.role || '').toLowerCase();
    if (r === 'owner' || r === 'admin' || r === 'member' || r === 'guest') {
      return r;
    }
    return null;
  }, [members, me]);

  const canManage = !isPersonal && (myRole === 'owner' || myRole === 'admin');

  useSetCrumbs(
    workspace
      ? [
          { label: workspace.name?.trim() || 'Untitled', to: `/workspaces/${workspace.id}` },
          { label: 'Members' },
        ]
      : [{ label: 'Members' }]
  );

  const excludeIds = useMemo(() => {
    const s = new Set<string>();
    for (const m of members) {
      s.add(m.id);
      if (m.user_id) s.add(m.user_id);
    }
    return s;
  }, [members]);

  const pendingInvites = useMemo(
    () => invitations.filter(i => i.status === 'pending'),
    [invitations],
  );
  const q = search.trim().toLowerCase();
  const filteredMembers = useMemo(
    () => filterWorkspaceMembers(members, q),
    [members, q],
  );
  const filteredPendingInvites = useMemo(
    () => filterWorkspaceInvitations(pendingInvites, q),
    [pendingInvites, q],
  );

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const [ws, m] = await Promise.all([
        workspacesApi.get(workspaceId),
        workspacesApi.listMembers(workspaceId),
      ]);
      setWorkspace(ws);
      setMembers(m);
      // Invitations only readable to members; tolerate 403 silently.
      if (ws.kind !== 'personal') {
        try {
          const invs = await workspacesApi.listInvitations(workspaceId);
          setInvitations(invs as unknown as Invitation[]);
        } catch {
          setInvitations([]);
        }
      } else {
        setInvitations([]);
      }
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to load members';
      setError(String(msg));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    load();
  }, [load]);

  const inviteRegisteredUser = async (u: User, role: string) => {
    if (!workspaceId) return;
    const email = (u.email || '').trim();
    if (!email) {
      showToast('This user has no email on file — use Invite by email instead', 'error');
      return;
    }
    setInviting(true);
    try {
      await invitationsApi.create(workspaceId, {
        email,
        role: role as Exclude<WorkspaceRole, 'owner'>
      });
      showToast('Invitation sent', 'success');
      load();
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string; message?: string } } })
          ?.response?.data?.detail
        || (e as { response?: { data?: { message?: string } } })?.response?.data
          ?.message
        || 'Failed to send invitation',
        'error'
      );
    } finally {
      setInviting(false);
    }
  };

  const patchMember = async (
    memberUserId: string,
    body: {
      role?: 'admin' | 'member' | 'guest';
      can_create_apps?: boolean;
      can_create_tracks?: boolean;
    }
  ) => {
    if (!workspaceId) return;
    try {
      await workspacesApi.patchMember(workspaceId, memberUserId, body);
      showToast('Member updated', 'success');
      load();
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to update',
        'error'
      );
    }
  };

  const removeMember = async (memberId: string) => {
    if (!workspaceId) return;
    const ok = await confirm({
      title: 'Remove member',
      message: 'Remove this member from the workspace?',
      confirmLabel: 'Remove',
      variant: 'danger'
    });
    if (!ok) return;
    try {
      await workspacesApi.removeMember(workspaceId, memberId);
      showToast('Member removed', 'success');
      load();
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to remove',
        'error'
      );
    }
  };

  const sendInvitation = async () => {
    if (!workspaceId) return;
    const trimmed = inviteEmail.trim();
    if (!trimmed) {
      showToast('Email is required', 'error');
      return;
    }
    setInviting(true);
    setLastAcceptanceUrl(null);
    try {
      const res = await invitationsApi.create(workspaceId, {
        email: trimmed,
        role: inviteRole,
        message: inviteMessage.trim() || undefined
      });
      setLastAcceptanceUrl(res.acceptance_url);
      setInviteEmail('');
      setInviteMessage('');
      showToast('Invitation sent', 'success');
      load();
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to send invitation',
        'error'
      );
    } finally {
      setInviting(false);
    }
  };

  const revokeInvitation = async (invitationId: string) => {
    if (!workspaceId) return;
    const ok = await confirm({
      title: 'Revoke invitation',
      message: 'Revoke this pending invitation? The token will stop working.',
      confirmLabel: 'Revoke',
      variant: 'danger'
    });
    if (!ok) return;
    try {
      await invitationsApi.revoke(workspaceId, invitationId);
      showToast('Invitation revoked', 'success');
      load();
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to revoke',
        'error'
      );
    }
  };

  const copyAcceptanceUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      showToast('Acceptance URL copied', 'success');
    } catch {
      showToast('Copy failed — select and copy manually', 'error');
    }
  };

  if (loading && !workspace) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <Skeleton className="h-32 rounded-lg" />
      </div>
    );
  }

  if (!workspace) {
    return (
      <div className="max-w-xl mx-auto px-4 md:px-6 py-12 md:py-16 text-center text-[var(--text-muted)]">
        {error || 'Not found'}
        <Link to="/workspaces" className="block mt-4 text-[var(--link)] hover:text-[var(--link-hover)]">
          ← Workspaces
        </Link>
      </div>
    );
  }

  return (
    <PageShell>
      <PageSection>
        <header className="mb-8 md:mb-10">
          <PageHeading>Members</PageHeading>
          <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
            <span>
              {members.length} {members.length === 1 ? 'member' : 'members'}
              {q ? ` · showing ${filteredMembers.length}` : ''}
            </span>
            <span aria-hidden>·</span>
            <Link
              to={`/workspaces/${workspace.id}`}
              className="hover:text-[var(--text)] transition-colors duration-fast"
            >
              {workspace.name}
            </Link>
            {pendingInvites.length > 0 && (
              <>
                <span aria-hidden>·</span>
                <span>
                  {pendingInvites.length} pending invitation
                  {pendingInvites.length === 1 ? '' : 's'}
                </span>
              </>
            )}
          </div>
          <p className="text-sm text-[var(--text-muted)] mt-3 max-w-2xl">
            {isPersonal
              ? `Personal workspaces have a single owner — that's you.`
              : `Owners and admins invite teammates by email or from the
              registered-user directory. Invitations must be accepted before
              access is granted. Guests see only the apps/tracks they're
              explicitly added to.`}
          </p>
        </header>
      </PageSection>

      <PageSection.Separator />

      <PageSection className={filterBar.sectionTop}>
        {error && (
          <div className="mb-4 text-sm text-[var(--danger-fg)]" role="alert">
            {error}{' '}
            <button type="button" className="underline" onClick={load}>
              Retry
            </button>
          </div>
        )}

        {canManage && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-6">
            <form
              className="app-card p-5 space-y-4"
              onSubmit={e => {
                e.preventDefault();
                sendInvitation();
              }}
              aria-label="Invite by email"
            >
              <h2 className="text-sm font-semibold text-[var(--text)]">
                Invite by email
              </h2>
              <div className="flex flex-col gap-3">
                <div>
                  <label
                    htmlFor="invite-email"
                    className="text-xs font-medium text-[var(--text-muted)] block mb-1"
                  >
                    Email address
                  </label>
                  <input
                    id="invite-email"
                    type="email"
                    autoComplete="email"
                    className="app-input"
                    placeholder="teammate@example.com"
                    value={inviteEmail}
                    onChange={e => setInviteEmail(e.target.value)}
                    disabled={inviting}
                    required
                  />
                </div>
                <div className="flex gap-2 items-end">
                  <div className="flex-1">
                    <label
                      htmlFor="invite-role"
                      className="text-xs font-medium text-[var(--text-muted)] block mb-1"
                    >
                      Role
                    </label>
                    <select
                      id="invite-role"
                      className="app-input"
                      value={inviteRole}
                      onChange={e =>
                        setInviteRole(
                          e.target.value as Exclude<WorkspaceRole, 'owner'>
                        )
                      }
                      disabled={inviting}
                    >
                      {ASSIGNABLE_ROLES.map(r => (
                        <option key={r} value={r}>
                          {r.charAt(0).toUpperCase() + r.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    icon={<Mail size={14} strokeWidth={LINE_ICON_STROKE} />}
                    disabled={inviting || !inviteEmail.trim()}
                  >
                    {inviting ? 'Sending…' : 'Send invite'}
                  </Button>
                </div>
                <div>
                  <label
                    htmlFor="invite-message"
                    className="text-xs font-medium text-[var(--text-muted)] block mb-1"
                  >
                    Note (optional)
                  </label>
                  <textarea
                    id="invite-message"
                    className="app-input"
                    placeholder="Anything you want them to know"
                    rows={2}
                    value={inviteMessage}
                    onChange={e => setInviteMessage(e.target.value)}
                    disabled={inviting}
                  />
                </div>
              </div>
              {lastAcceptanceUrl && (
                <div
                  className="rounded-md border border-[var(--panel-border)] bg-[var(--panel-2)] p-3 text-xs"
                  role="status"
                  aria-live="polite"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-[var(--text-muted)]">
                      Email sent. You can also share the link directly:
                    </p>
                    <button
                      type="button"
                      onClick={() => copyAcceptanceUrl(lastAcceptanceUrl)}
                      className="inline-flex items-center justify-center w-9 h-9 rounded hover:bg-[var(--panel)] text-[var(--text-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]"
                      aria-label="Copy acceptance URL to clipboard"
                      title="Copy URL"
                    >
                      <Copy size={14} strokeWidth={LINE_ICON_STROKE} />
                    </button>
                  </div>
                  <p className="mt-1.5 break-all text-[var(--text)] font-mono">
                    {lastAcceptanceUrl}
                  </p>
                </div>
              )}
            </form>

            <div className="app-card p-5 space-y-4">
              <h2 className="text-sm font-semibold text-[var(--text)]">
                Invite registered user
              </h2>
              <UserSearchPicker
                excludeIds={excludeIds}
                onSelect={u => inviteRegisteredUser(u, pickerRole)}
                label="Search registered users"
              />
              <div>
                <label
                  htmlFor="picker-role"
                  className="text-xs font-medium text-[var(--text-muted)] block mb-1"
                >
                  Invite as role
                </label>
                <select
                  id="picker-role"
                  className="app-input"
                  value={pickerRole}
                  onChange={e =>
                    setPickerRole(
                      e.target.value as Exclude<WorkspaceRole, 'owner'>
                    )
                  }
                >
                  {ASSIGNABLE_ROLES.map(r => (
                    <option key={r} value={r}>
                      {r.charAt(0).toUpperCase() + r.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}

        <SearchRow>
          <PageSearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search members…"
            ariaLabel="Search members by name, email, or role"
          />
        </SearchRow>

        {filteredPendingInvites.length > 0 && (
          <div className="app-card mb-6">
            <div className="px-5 py-3 border-b border-[var(--panel-border)] text-sm font-semibold text-[var(--text)]">
              Pending invitations
            </div>
            <ul className="divide-y divide-[var(--panel-border)]">
              {filteredPendingInvites.map(inv => (
                <li
                  key={inv.id}
                  className="flex flex-col gap-2 px-5 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-[var(--text)] truncate">
                      {inv.email}
                    </p>
                    <p className="text-xs text-[var(--text-muted)]">
                      Expires {inv.expires_at?.slice(0, 10) || '—'}
                      {inv.message ? ` · "${inv.message}"` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <RoleBadge role={inv.role} />
                    <InviteStatusPill status={inv.status} />
                    {canManage && (
                      <button
                        type="button"
                        onClick={() => revokeInvitation(inv.id)}
                        className="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[var(--danger-fg)] hover:bg-[var(--danger-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]"
                        aria-label={`Revoke invitation for ${inv.email}`}
                      >
                        <Trash2 size={14} strokeWidth={LINE_ICON_STROKE} />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="app-card divide-y divide-[var(--panel-border)]">
          {loading ? (
            <p className="p-4 text-sm text-[var(--text-muted)]">Loading…</p>
          ) : filteredMembers.length === 0 ? (
            <p className="p-4 text-sm text-[var(--text-muted)]">
              {q && members.length > 0
                ? 'No members match your search.'
                : 'No members yet.'}
            </p>
          ) : (
            filteredMembers.map(m => {
              const rawRole = String(m.role || 'member').toLowerCase();
              const isRowOwner = rawRole === 'owner';
              const memberKey = m.user_id || m.id;
              const canEditRow =
                canManage && !isRowOwner && !isSamePrincipal(me, memberKey);
              const canRemove =
                canManage && !isRowOwner && !isSamePrincipal(me, memberKey);
              return (
                <div
                  key={m.id}
                  className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <AvatarStackedMeta
                    className="min-w-0 flex-1"
                    avatar={
                      <Avatar
                        name={m.display_name}
                        size="sm"
                        attachmentId={m.avatar_attachment_id}
                        userId={m.id}
                        version={m.updated_at}
                      />
                    }
                    primary={
                      <div className="flex items-center gap-2 min-w-0">
                        <p className="font-medium text-[var(--text)] truncate">
                          {m.display_name}
                        </p>
                        <RoleBadge role={rawRole} />
                      </div>
                    }
                    secondary={
                      <p className="text-xs text-[var(--text-muted)] truncate">
                        {m.email || m.id}
                      </p>
                    }
                  />
                  <div className="flex flex-col items-stretch gap-2 shrink-0 sm:items-end">
                    {canEditRow && memberKey ? (
                      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text)]">
                        <select
                          className="app-input"
                          value={rawRole}
                          aria-label={`Change role for ${m.display_name}`}
                          onChange={e =>
                            patchMember(memberKey, {
                              role: e.target.value as 'admin' | 'member' | 'guest'
                            })
                          }
                        >
                          {ASSIGNABLE_ROLES.map(r => (
                            <option key={r} value={r}>
                              {r.charAt(0).toUpperCase() + r.slice(1)}
                            </option>
                          ))}
                        </select>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={Boolean(m.can_create_apps)}
                            onChange={e =>
                              patchMember(memberKey, {
                                can_create_apps: e.target.checked
                              })
                            }
                          />
                          Apps
                        </label>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={Boolean(m.can_create_tracks)}
                            onChange={e =>
                              patchMember(memberKey, {
                                can_create_tracks: e.target.checked
                              })
                            }
                          />
                          Tracks
                        </label>
                      </div>
                    ) : null}
                    {isRowOwner ? (
                      <p className="text-[12px] text-[var(--text-muted)]">
                        {isPersonal
                          ? 'You own this personal workspace'
                          : 'Owner has full workspace access'}
                      </p>
                    ) : null}
                    <div className="flex justify-end">
                      {canRemove && (
                        <button
                          type="button"
                          onClick={() => removeMember(m.id)}
                          className="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[var(--danger-fg)] hover:bg-[var(--danger-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]"
                          aria-label={`Remove ${m.display_name}`}
                        >
                          <Trash2 size={16} strokeWidth={LINE_ICON_STROKE} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </PageSection>
    </PageShell>
  );
}
