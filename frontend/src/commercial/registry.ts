/**
 * Commercial UI extension slots. Open-source Core leaves these empty;
 * Business overlays `commercial/register.ts` to fill them.
 */
import type { ComponentType, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

export type SettingsSectionRegistration = {
  id: string;
  label: string;
  icon: LucideIcon;
  render: () => ReactNode;
};

export type AdminNavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
};

export type AdminRouteRegistration = {
  path: string;
  element: ReactNode;
};

export type SidebarAccountAction = {
  id: string;
  label: string;
  onSelect: () => void;
};

export type PaywallDecision = {
  blocked: boolean;
  reason: 'plan' | null;
  min_plan: string | null;
  plan_title: string | null;
};

export type AppManagerPaywall = {
  loadForWorkspace: (workspaceId: string) => Promise<{
    status: unknown;
    catalog: unknown;
  }>;
  decide: (
    slug: string,
    status: unknown,
    catalog: unknown,
  ) => PaywallDecision;
  label: (decision: PaywallDecision, name: string) => string | null;
  openBilling: () => void;
  /** Plan filter tabs for Available apps (e.g. all | free | basic | premium). */
  catalogPlanTiers: (catalog: unknown) => string[];
  /** Which plan tab a library profile belongs on. */
  profilePlanTab: (profile: { id?: string; [key: string]: unknown }, catalog: unknown) => string;
};

export type WorkspacePlanChrome = {
  /** Render plan badge for workspace list/detail/switcher rows. */
  renderBadge: (props: {
    plan_key?: string | null;
    plan_label?: string | null;
    subscription_status?: string | null;
    cancel_at_period_end?: boolean | null;
    className?: string;
    compact?: boolean;
  }) => ReactNode;
  /** Navigate to billing settings (e.g. from workspace detail). */
  openBilling?: () => void;
};

type LayoutBanner = ComponentType;

const settingsSections: SettingsSectionRegistration[] = [];
const adminNav: AdminNavItem[] = [];
const adminRoutes: AdminRouteRegistration[] = [];
const sidebarActions: SidebarAccountAction[] = [];
const layoutBanners: LayoutBanner[] = [];
let appManagerPaywall: AppManagerPaywall | null = null;
let workspacePlanChrome: WorkspacePlanChrome | null = null;
let composerQuotaHint: ComponentType<{ workspaceId: string }> | null = null;

export function registerSettingsSection(section: SettingsSectionRegistration): void {
  const i = settingsSections.findIndex(s => s.id === section.id);
  if (i >= 0) settingsSections[i] = section;
  else settingsSections.push(section);
}

export function getRegisteredSettingsSections(): SettingsSectionRegistration[] {
  return [...settingsSections];
}

export function registerAdminNavItem(item: AdminNavItem): void {
  const i = adminNav.findIndex(n => n.to === item.to);
  if (i >= 0) adminNav[i] = item;
  else adminNav.push(item);
}

export function getRegisteredAdminNav(): AdminNavItem[] {
  return [...adminNav];
}

export function registerAdminRoute(route: AdminRouteRegistration): void {
  const i = adminRoutes.findIndex(r => r.path === route.path);
  if (i >= 0) adminRoutes[i] = route;
  else adminRoutes.push(route);
}

export function getRegisteredAdminRoutes(): AdminRouteRegistration[] {
  return [...adminRoutes];
}

export function registerSidebarAccountAction(action: SidebarAccountAction): void {
  const i = sidebarActions.findIndex(a => a.id === action.id);
  if (i >= 0) sidebarActions[i] = action;
  else sidebarActions.push(action);
}

export function getRegisteredSidebarAccountActions(): SidebarAccountAction[] {
  return [...sidebarActions];
}

export function registerLayoutBanner(Banner: LayoutBanner): void {
  if (!layoutBanners.includes(Banner)) layoutBanners.push(Banner);
}

export function getRegisteredLayoutBanners(): LayoutBanner[] {
  return [...layoutBanners];
}

export function registerAppManagerPaywall(adapter: AppManagerPaywall | null): void {
  appManagerPaywall = adapter;
}

export function getAppManagerPaywall(): AppManagerPaywall | null {
  return appManagerPaywall;
}

export function registerWorkspacePlanChrome(chrome: WorkspacePlanChrome | null): void {
  workspacePlanChrome = chrome;
}

export function getWorkspacePlanChrome(): WorkspacePlanChrome | null {
  return workspacePlanChrome;
}

export function registerComposerQuotaHint(
  Comp: ComponentType<{ workspaceId: string }> | null,
): void {
  composerQuotaHint = Comp;
}

export function getComposerQuotaHint(): ComponentType<{
  workspaceId: string;
}> | null {
  return composerQuotaHint;
}
