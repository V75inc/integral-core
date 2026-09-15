import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Trash2 } from 'lucide-react';
import { adminApi } from '../../api/admin';
import {
  Badge,
  Button,
  LINE_ICON_STROKE,
  PageHeading,
  PageSection,
  PageShell,
  Skeleton,
} from '../../components/ui';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { useSetCrumbs } from '../../context/CrumbsContext';
import { invalidateAfterAdminWorkspaceDelete } from '../../queryKeys';
import { AdminEntityLink, AdminOwnerCell } from '../../components/admin/AdminEntityLink';

export function AdminWorkspaceDetailPage() {
  const { workspaceId = '' } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const { data: workspace, isPending, error, refetch } = useQuery({
    queryKey: ['admin', 'workspace', workspaceId],
    queryFn: () => adminApi.getWorkspace(workspaceId),
    enabled: !!workspaceId,
  });

  const { data: membersData } = useQuery({
    queryKey: ['admin', 'workspace-members', workspaceId],
    queryFn: () => adminApi.listWorkspaceMembers(workspaceId, { per_page: 100 }),
    enabled: !!workspaceId,
  });

  useEffect(() => {
    if (workspace) {
      setName(workspace.name);
      setDescription(workspace.description);
    }
  }, [workspace]);

  useSetCrumbs([
    { label: 'Admin', to: '/admin' },
    { label: 'Workspaces', to: '/admin/workspaces' },
    { label: workspace?.name || 'Workspace' },
  ]);

  async function handleSave() {
    if (!workspace) return;
    setSaving(true);
    try {
      await adminApi.updateWorkspace(workspace.id, {
        name: name.trim(),
        description,
      });
      showToast('Workspace updated', 'success');
      await queryClient.invalidateQueries({ queryKey: ['admin', 'workspace', workspaceId] });
    } catch {
      showToast('Could not update workspace', 'error');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!workspace) return;
    const ok = await confirm({
      title: 'Delete workspace',
      message: `Permanently delete "${workspace.name}" and all contained resources?`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      const deletedId = workspace.id;
      await adminApi.deleteWorkspace(deletedId);
      await invalidateAfterAdminWorkspaceDelete(queryClient, deletedId);
      showToast('Workspace deleted', 'success');
      navigate('/admin/workspaces');
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { message?: string } } })?.response?.data?.message;
      showToast(msg || 'Could not delete workspace', 'error');
    }
  }

  return (
    <PageShell>
      <PageSection>
        <Link
          to="/admin/workspaces"
          className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <ArrowLeft size={14} strokeWidth={LINE_ICON_STROKE} />
          Back to workspaces
        </Link>
        {isPending && <Skeleton className="mt-6 h-32 w-full rounded-xl" />}
        {error && (
          <p className="mt-6 text-sm text-[var(--danger-text)]">
            Could not load workspace.{' '}
            <button type="button" className="underline" onClick={() => refetch()}>Retry</button>
          </p>
        )}
        {workspace && (
          <div className="mt-6">
            <PageHeading>{workspace.name}</PageHeading>
            <p className="mt-1 text-sm capitalize text-[var(--text-muted)]">{workspace.kind}</p>
            {workspace.owner ? (
              <div className="mt-2 text-sm">
                <span className="text-[var(--text-subtle)]">Owner: </span>
                <AdminOwnerCell owner={workspace.owner} />
              </div>
            ) : null}
            <div className="mt-2 flex gap-2">
              <Badge>{workspace.member_count} members</Badge>
              <Badge>{workspace.app_count} apps</Badge>
              <Badge>{workspace.track_count} tracks</Badge>
            </div>
            <Link
              to={`/workspaces/${workspace.id}`}
              className="mt-3 inline-block text-sm text-[var(--link)]"
            >
              Open in app →
            </Link>
          </div>
        )}
      </PageSection>

      {workspace && (
        <>
          <PageSection className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-[var(--text-subtle)]">Edit</h2>
            <div className="mt-4 grid gap-4 max-w-lg">
              <label className="block text-sm">
                <span className="text-[var(--text-muted)]">Name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                <span className="text-[var(--text-muted)]">Description</span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                />
              </label>
              <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
            </div>
          </PageSection>

          <PageSection className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-[var(--text-subtle)]">Members</h2>
            <div className="mt-4 space-y-2">
              {(membersData?.members ?? []).map((m) => (
                <div key={m.id} className="flex items-center justify-between rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm">
                  <div>
                    <AdminEntityLink
                      type="user"
                      id={m.id}
                      label={m.display_name || m.email || m.id}
                    />
                    {m.email && m.display_name ? (
                      <p className="text-[var(--text-muted)]">{m.email}</p>
                    ) : null}
                  </div>
                  <span className="capitalize text-[var(--text-muted)]">{m.role}</span>
                </div>
              ))}
            </div>
          </PageSection>

          <PageSection className="mt-8">
            <Button variant="danger" onClick={handleDelete}>
              <Trash2 size={14} className="mr-1.5 inline" />
              Delete workspace
            </Button>
          </PageSection>
        </>
      )}
    </PageShell>
  );
}
