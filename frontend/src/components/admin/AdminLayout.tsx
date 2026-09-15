import { NavLink, Outlet } from 'react-router-dom';
import {
  Building2,
  LayoutGrid,
  List,
  Shield,
  Users,
} from 'lucide-react';
import { LINE_ICON_STROKE } from '../ui';

const NAV = [
  { to: '/admin', label: 'Overview', icon: Shield, end: true },
  { to: '/admin/users', label: 'Users', icon: Users, end: false },
  { to: '/admin/workspaces', label: 'Workspaces', icon: Building2, end: false },
  { to: '/admin/apps', label: 'Apps', icon: LayoutGrid, end: false },
  { to: '/admin/tracks', label: 'Tracks', icon: List, end: false },
] as const;

/**
 * Admin section chrome — lives inside the main Layout so platform admins
 * get the same sidebar, top bar, and page rhythm as the rest of the app.
 * This wrapper only adds the horizontal admin sub-nav above page content.
 */
export function AdminLayout() {
  return (
    <div className="min-w-0">
      <div className="px-3 md:px-16 pt-2 pb-0">
        <div className="min-w-0 w-full max-w-page mx-auto">
          <nav
            aria-label="Platform admin"
            className="flex flex-wrap items-center gap-1 border-b border-[var(--border-subtle)] pb-3"
          >
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  [
                    'inline-flex items-center gap-2 rounded-[var(--radius-input)] px-3 py-1.5',
                    'text-xs transition-colors duration-fast',
                    isActive
                      ? 'bg-[var(--nav-active-bg)] text-[var(--text)] font-medium'
                      : 'text-[var(--text-subtle)] hover:text-[var(--text-muted)] hover:bg-[var(--panel)]',
                  ].join(' ')
                }
              >
                <Icon size={13} strokeWidth={LINE_ICON_STROKE} />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </div>
      <Outlet />
    </div>
  );
}
