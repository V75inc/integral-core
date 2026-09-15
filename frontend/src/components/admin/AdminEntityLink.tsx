import { Link } from 'react-router-dom';

export type AdminEntityType = 'user' | 'workspace' | 'app' | 'track';

export interface AdminOwnerSummary {
  id: string;
  display_name: string;
  email: string;
}

export function adminEntityPath(type: AdminEntityType, id: string): string {
  switch (type) {
    case 'user':
      return `/admin/users/${id}`;
    case 'workspace':
      return `/admin/workspaces/${id}`;
    case 'app':
      return `/admin/apps/${id}`;
    case 'track':
      return `/admin/tracks/${id}`;
  }
}

export function AdminEntityLink({
  type,
  id,
  label,
  className,
  fallback = '—',
}: {
  type: AdminEntityType;
  id?: string | null;
  label?: string | null;
  className?: string;
  fallback?: string;
}) {
  if (!id) {
    return <span className={className ?? 'text-[var(--text-muted)]'}>{fallback}</span>;
  }
  const text = label?.trim() || id;
  return (
    <Link
      to={adminEntityPath(type, id)}
      className={className ?? 'font-medium text-[var(--link)] hover:underline'}
    >
      {text}
    </Link>
  );
}

export function AdminOwnerCell({ owner }: { owner?: AdminOwnerSummary | null }) {
  if (!owner?.id) {
    return <span className="text-[var(--text-muted)]">—</span>;
  }
  return (
    <div>
      <AdminEntityLink
        type="user"
        id={owner.id}
        label={owner.display_name || owner.email || owner.id}
      />
      {owner.email && owner.display_name ? (
        <p className="text-xs text-[var(--text-muted)]">{owner.email}</p>
      ) : null}
    </div>
  );
}
