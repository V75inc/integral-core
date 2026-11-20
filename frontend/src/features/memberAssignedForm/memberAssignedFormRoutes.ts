import type { User } from '../../types';

/** Authenticated member assigned-entry wizard, independent of an app. */
export const MEMBER_ASSIGNED_FORM_PATH = '/me/assigned-form';

export function pendingAssignedFormUrl(user: User | null | undefined): string {
  return user?.pending_assigned_form?.url?.trim() || '';
}

/** Resolve stored entry references to the canonical authenticated route. */
export function resolveMemberAssignedFormNavigateTarget(rawUrl: string): string {
  try {
    const parsed = new URL(String(rawUrl || '').trim(), 'http://local.invalid');
    if (parsed.pathname === MEMBER_ASSIGNED_FORM_PATH) {
      return `${MEMBER_ASSIGNED_FORM_PATH}${parsed.search}`;
    }
    const entry = parsed.searchParams.get('entry')?.trim();
    if (entry) return `${MEMBER_ASSIGNED_FORM_PATH}?entry=${encodeURIComponent(entry)}`;
  } catch { /* Missing or malformed references resolve to the host-owned assignment. */ }
  return MEMBER_ASSIGNED_FORM_PATH;
}

export function isMemberAssignedFormPathname(pathname: string): boolean {
  return String(pathname || '').replace(/\/+$/, '') === MEMBER_ASSIGNED_FORM_PATH;
}
