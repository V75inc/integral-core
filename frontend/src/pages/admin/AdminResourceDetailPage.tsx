import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { adminApi } from '../../api/admin';
import {
  Badge,
  LINE_ICON_STROKE,
  PageHeading,
  PageSection,
  PageShell,
  Skeleton,
} from '../../components/ui';
import { useSetCrumbs } from '../../context/CrumbsContext';
import { AdminEntityLink, AdminOwnerCell } from '../../components/admin/AdminEntityLink';

export function AdminResourceDetailPage({
  resourceType,
}: {
  resourceType: 'app' | 'track';
}) {
  const params = useParams<{ appId?: string; trackId?: string }>();
  const resourceId = resourceType === 'app' ? params.appId : params.trackId;
  const listPath = resourceType === 'app' ? '/admin/apps' : '/admin/tracks';
  const label = resourceType === 'app' ? 'Apps' : 'Tracks';

  const { data: resource, isPending, error, refetch } = useQuery({
    queryKey: ['admin', resourceType, resourceId],
    queryFn: () =>
      resourceType === 'app'
        ? adminApi.getApp(resourceId!)
        : adminApi.getTrack(resourceId!),
    enabled: !!resourceId,
  });

  useSetCrumbs([
    { label: 'Admin', to: '/admin' },
    { label, to: listPath },
    { label: resource?.name || label },
  ]);

  return (
    <PageShell>
      <PageSection>
        <Link
          to={listPath}
          className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <ArrowLeft size={14} strokeWidth={LINE_ICON_STROKE} />
          Back to {label.toLowerCase()}
        </Link>
        {isPending && <Skeleton className="mt-6 h-32 w-full rounded-xl" />}
        {error && (
          <p className="mt-6 text-sm text-[var(--danger-text)]">
            Could not load {resourceType}.{' '}
            <button type="button" className="underline" onClick={() => refetch()}>
              Retry
            </button>
          </p>
        )}
        {resource && (
          <div className="mt-6">
            <PageHeading>{resource.name || resource.id}</PageHeading>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge variant="info">{resourceType}</Badge>
              {resource.visibility ? (
                <Badge>{resource.visibility}</Badge>
              ) : null}
            </div>
            <dl className="mt-6 grid gap-3 text-sm max-w-xl">
              <div>
                <dt className="text-[var(--text-subtle)]">ID</dt>
                <dd className="font-mono text-xs mt-0.5 break-all">{resource.id}</dd>
              </div>
              <div>
                <dt className="text-[var(--text-subtle)]">Workspace</dt>
                <dd className="mt-0.5">
                  <AdminEntityLink
                    type="workspace"
                    id={resource.workspace_id}
                    label={resource.workspace_name}
                  />
                </dd>
              </div>
              <div>
                <dt className="text-[var(--text-subtle)]">Owner</dt>
                <dd className="mt-0.5">
                  <AdminOwnerCell owner={resource.owner} />
                </dd>
              </div>
              {resource.created_at ? (
                <div>
                  <dt className="text-[var(--text-subtle)]">Created</dt>
                  <dd className="mt-0.5">{resource.created_at}</dd>
                </div>
              ) : null}
            </dl>
            <p className="mt-6 text-sm text-[var(--text-muted)]">
              Platform admin view — open the workspace above to manage this{' '}
              {resourceType} in context.
            </p>
          </div>
        )}
      </PageSection>
    </PageShell>
  );
}
