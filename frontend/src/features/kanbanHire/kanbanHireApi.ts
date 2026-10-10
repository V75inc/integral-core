import apiClient from '../../api/client';
import { appsApi } from '../../api/apps';
import { entriesApi } from '../../api/entries';
import { workspacesApi } from '../../api/workspaces';
import type { App, Entry, Track } from '../../types';

/** rc10 `types` exports `WorkspaceSummary`, not `Workspace` — keep hire overlay self-contained. */
type HireWorkspaceRef = {
  id: string;
  kind?: string;
  workspace_type?: string;
};

export type ContractTemplateOption = {
  id: string;
  name: string;
  hasPublishedVersion?: boolean;
};

function slugify(value: string): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '');
}

export function fieldString(entry: Entry, key: string): string {
  const raw = entry.custom_fields?.[key];
  if (raw == null) return '';
  if (Array.isArray(raw)) return String(raw[0] || '').trim();
  return String(raw).trim();
}

/** Loose check aligned with server EmailStr (must contain @, no spaces). */
export function looksLikeEmail(value: string): boolean {
  const v = String(value || '').trim();
  if (!v || /\s/.test(v) || !v.includes('@')) return false;
  const [, domain] = v.split('@');
  return Boolean(domain && domain.includes('.'));
}

export function hireFlowErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) {
    const msg = err.message.trim();
    if (msg && msg !== fallback) return msg;
  }
  const ax = err as {
    response?: {
      data?: {
        message?: unknown;
        detail?: unknown;
        details?: { errors?: Array<{ loc?: unknown[]; msg?: string }> };
      };
    };
  };
  const data = ax?.response?.data;
  const fieldErrors = data?.details?.errors;
  if (Array.isArray(fieldErrors) && fieldErrors.length) {
    const parts = fieldErrors.map(e => {
      const loc = Array.isArray(e.loc) ? e.loc.filter(x => x !== 'body').join('.') : '';
      const msg = String(e.msg || '').trim();
      if (!msg) return '';
      if (loc === 'email') return `Work email: ${msg}`;
      if (loc === 'credentials_email') return `Personal email (on candidate): ${msg}`;
      return loc ? `${loc}: ${msg}` : msg;
    });
    const joined = parts.filter(Boolean).join('; ');
    if (joined) return joined;
  }
  if (data && typeof data === 'object') {
    const message = (data as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim() && message.trim() !== 'Validation failed') {
      return message.trim();
    }
  }
  return fallback;
}

function appPackageSlug(app: App): string {
  const raw = app as App & { installed_package_slug?: string; package_slug?: string };
  return String(raw.installed_package_slug || raw.package_slug || '').trim().toLowerCase();
}

function isRecruitmentApp(app: App): boolean {
  if (appPackageSlug(app) === 'recruitment-app') return true;
  return slugify(app.name || '') === 'recruitment';
}

function workspaceKind(ws: HireWorkspaceRef): string {
  return String(ws.kind || ws.workspace_type || '')
    .trim()
    .toLowerCase();
}

function isPersonalWorkspaceKind(kind: string): boolean {
  return kind === 'personal';
}

/** Org workspace for member provision — never the scope Personal workspace by mistake. */
export async function resolveHireWorkspaceId(opts: {
  candidate: Entry | null;
  hintWorkspaceId?: string;
  recruitmentAppId?: string;
  scopeWorkspace?: Pick<HireWorkspaceRef, 'id' | 'kind'> | null;
}): Promise<{ workspaceId: string; error?: string }> {
  const candidate = opts.candidate;
  const ordered: string[] = [];

  const push = (raw: string | null | undefined) => {
    const id = String(raw || '').trim();
    if (id && !ordered.includes(id)) ordered.push(id);
  };

  push(opts.hintWorkspaceId);
  push(candidate?.track?.workspace_id);
  push(candidate?.app?.workspace_id);

  if (opts.recruitmentAppId) {
    try {
      const app = await appsApi.get(opts.recruitmentAppId);
      push(app.workspace_id);
    } catch {
      /* ignore */
    }
  }

  try {
    const apps = await appsApi.list();
    const trackId = String(candidate?.track_id || candidate?.track?.id || '').trim();
    for (const app of apps) {
      if (!isRecruitmentApp(app)) continue;
      if (trackId) {
        try {
          const tracks = await appsApi.listTracks(app.id);
          if (tracks.some(t => String(t.id) === trackId)) {
            push(app.workspace_id);
            break;
          }
        } catch {
          push(app.workspace_id);
        }
      } else {
        push(app.workspace_id);
      }
    }
    if (!ordered.length) {
      for (const app of apps) {
        if (isRecruitmentApp(app)) push(app.workspace_id);
      }
    }
  } catch {
    /* ignore */
  }

  if (opts.scopeWorkspace && !isPersonalWorkspaceKind(String(opts.scopeWorkspace.kind || ''))) {
    push(opts.scopeWorkspace.id);
  }

  for (const id of ordered) {
    try {
      const ws = (await workspacesApi.get(id)) as HireWorkspaceRef;
      if (!isPersonalWorkspaceKind(workspaceKind(ws))) {
        return { workspaceId: id };
      }
    } catch {
      continue;
    }
  }

  const fallback = ordered[0] || String(opts.scopeWorkspace?.id || '').trim();
  if (fallback) {
    try {
      const ws = (await workspacesApi.get(fallback)) as HireWorkspaceRef;
      if (isPersonalWorkspaceKind(workspaceKind(ws))) {
        return {
          workspaceId: fallback,
          error:
            'Complete hire must run in an organization workspace. Switch the workspace switcher to your company org (where Recruitment and HR are installed), then try again.',
        };
      }
      return { workspaceId: fallback };
    } catch {
      /* fall through */
    }
  }

  return {
    workspaceId: fallback,
    error: ordered.length
      ? 'Could not find an organization workspace for this hire.'
      : 'Workspace context is missing.',
  };
}

export function contractTemplateId(entry: Entry): string {
  return (
    fieldString(entry, 'contract_template') || fieldString(entry, 'contract_template_id')
  );
}

export function suggestWorkEmail(name: string, domain?: string): string {
  const cleanedDomain = String(domain || '')
    .trim()
    .replace(/^@/, '');
  if (!cleanedDomain) return '';
  const parts = name
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const local =
    parts.length >= 2
      ? `${parts[0]}.${parts[parts.length - 1]}`
      : parts[0] || 'employee';
  return `${local.replace(/[^a-z0-9.]/g, '')}@${cleanedDomain}`;
}

function trackMatches(track: Track, key: string, title: string): boolean {
  if (slugify(track.template_id || '') === key) return true;
  return slugify(track.title || '') === slugify(title);
}

export async function listEmploymentContractTemplates(
  workspaceId: string,
): Promise<ContractTemplateOption[]> {
  try {
    const { data } = await apiClient.post('/tools/document_templates_list_active_templates', {
      input: {
        document_type: 'employment_contract',
        status: 'active',
        consumer_module: 'hr_app',
        require_published_version: true,
      },
    });
    const output =
      (data as {
        output?: {
          templates?: { id: string; name: string; has_published_version?: boolean }[];
        };
      })?.output ?? data;
    const rows =
      (output as {
        templates?: { id: string; name: string; has_published_version?: boolean }[];
      })?.templates ?? [];
    if (rows.length) {
      return rows.map(t => ({
        id: String(t.id),
        name: String(t.name || t.id),
        hasPublishedVersion: t.has_published_version !== false,
      }));
    }
  } catch {
    /* fall back to template track entries */
  }

  const apps = await appsApi.list();
  const documentsApp = apps.find(app => {
    if (app.workspace_id !== workspaceId) return false;
    const pkg = appPackageSlug(app);
    return pkg === 'document_templates';
  });
  if (!documentsApp) return [];

  const tracks = await appsApi.listTracks(documentsApp.id);
  const templatesTrack = tracks.find(t => trackMatches(t, 'templates', 'Templates'));
  if (!templatesTrack) return [];

  const entries = await entriesApi.list({
    track_id: templatesTrack.id,
    limit: 200,
  });
  return entries
    .filter(e => slugify(e.type || '') === 'template')
    .map(e => ({ id: e.id, name: (e.title || e.id).trim() }));
}

export async function persistCandidateContractTemplate(
  candidateId: string,
  templateId: string,
  existingCustomFields?: Record<string, unknown>,
): Promise<void> {
  await entriesApi.update(candidateId, {
    custom_fields: {
      ...(existingCustomFields || {}),
      contract_template: templateId,
    },
  });
}

/** Persist contract template and move candidate to Offer after readiness passes. */
export async function confirmKanbanCandidateOffer(
  candidate: Entry,
  contractTemplateId: string,
): Promise<Entry> {
  const templateId = contractTemplateId.trim();
  if (!templateId) {
    throw new Error('Choose an employment contract template.');
  }
  return entriesApi.update(candidate.id, {
    custom_fields: {
      ...(candidate.custom_fields || {}),
      contract_template: templateId,
      stage: 'offer',
    },
  });
}

export async function checkCandidateHireReadiness(
  candidate: Entry,
  templateId: string,
): Promise<{
  ok: boolean;
  missing_fields?: { key?: string; label?: string }[];
  error?: string;
}> {
  const { data } = await apiClient.post('/tools/recruitment_candidate_hire_readiness', {
    input: {
      entry_id: candidate.id,
      template_id: templateId,
      custom_fields: {
        ...(candidate.custom_fields || {}),
        contract_template: templateId,
      },
    },
  });
  const output = (data as { output?: Record<string, unknown> })?.output ?? data;
  return output as {
    ok: boolean;
    missing_fields?: { key?: string; label?: string }[];
    error?: string;
  };
}

export async function runCompleteHireTool(input: {
  entry_id: string;
  user_id: string;
  work_email: string;
  contract_template_id?: string;
}): Promise<{
  employee_id: string;
  onboarding_form_id?: string | null;
}> {
  try {
    const { data } = await apiClient.post('/tools/recruitment_complete_hire', {
      input,
    });
    const output = (data as { output?: Record<string, unknown> })?.output ?? data;
    return output as { employee_id: string; onboarding_form_id?: string | null };
  } catch (err) {
    throw new Error(hireFlowErrorMessage(err, 'Complete hire tool failed'));
  }
}

function hireProvisionError(err: unknown): Error {
  const ax = err as { response?: { status?: number; data?: { message?: string } } };
  if (ax.response?.status === 405) {
    return new Error(
      'Workspace member provisioning is unavailable on this server. Rebuild the API image (docker compose build api) so the hire endpoint patch is applied.',
    );
  }
  const msg = ax.response?.data?.message;
  if (typeof msg === 'string' && msg.trim()) {
    const trimmed = msg.trim();
    if (/personal workspaces have no members/i.test(trimmed)) {
      return new Error(
        'Complete hire must run in an organization workspace. Switch the workspace switcher to your company org (where Recruitment and HR are installed), then try again.',
      );
    }
  }
  const detail = hireFlowErrorMessage(err, 'Member provision failed');
  return new Error(detail);
}

async function provisionMember(
  workspaceId: string,
  entry: Entry,
  workEmail: string,
): Promise<{ user_id: string; credentials_emailed: boolean }> {
  const personalEmail = fieldString(entry, 'email');
  const displayName = (entry.title || '').trim() || personalEmail;
  if (!personalEmail) {
    throw new Error('Candidate personal email is required.');
  }
  let data: unknown;
  try {
    ({ data } = await apiClient.post(
    `/workspaces/${encodeURIComponent(workspaceId)}/members/provision`,
    {
      email: workEmail.trim(),
      display_name: displayName,
      role: 'member',
      send_credentials: true,
      credentials_email: personalEmail,
    },
    ));
  } catch (err) {
    throw hireProvisionError(err);
  }
  const row = (data as { user_id?: string; credentials_emailed?: boolean }) || data;
  const userId = String(row.user_id || '').trim();
  if (!userId) {
    throw new Error('Member provision did not return a user id.');
  }
  return {
    user_id: userId,
    credentials_emailed: Boolean(row.credentials_emailed),
  };
}

async function setOnboardingPrompt(
  workspaceId: string,
  userId: string,
  formUrl: string,
  options?: { recipientEmail?: string; recipientName?: string },
): Promise<{ emailed: boolean }> {
  try {
    const personalEmail = options?.recipientEmail?.trim() || '';
    const { data } = await apiClient.post(
      `/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}/assigned-form-prompt`,
      {
        form_url: formUrl,
        send_email: Boolean(personalEmail),
        recipient_email: personalEmail || undefined,
        recipient_name: options?.recipientName?.trim() || undefined,
        form_title: 'employee onboarding form',
      },
    );
    const row = (data as { emailed?: boolean }) || {};
    return { emailed: Boolean(row.emailed) };
  } catch {
    return { emailed: false };
  }
}

export async function resolveHrAppId(workspaceId: string): Promise<string | null> {
  const apps = await appsApi.list();
  const match = apps.find(app => {
    if (app.workspace_id !== workspaceId) return false;
    if (appPackageSlug(app) === 'hr_app') return true;
    const name = slugify(app.name || '');
    return name === 'hr' || name === 'human_resources';
  });
  return match?.id ?? null;
}

export async function completeKanbanCandidateHire(opts: {
  candidate: Entry;
  workspaceId: string;
  workEmail: string;
  contractTemplateId: string;
}): Promise<{
  employeeId: string;
  credentialsEmailed: boolean;
  formEmailed: boolean;
  formUrl: string | null;
}> {
  const templateId = opts.contractTemplateId.trim();
  if (!templateId) {
    throw new Error('Select an employment contract template.');
  }

  await persistCandidateContractTemplate(
    opts.candidate.id,
    templateId,
    opts.candidate.custom_fields,
  );

  const candidateForHire: Entry = {
    ...opts.candidate,
    custom_fields: { ...(opts.candidate.custom_fields || {}), contract_template: templateId },
  };

  const provisioned = await provisionMember(opts.workspaceId, candidateForHire, opts.workEmail);

  const hireResult = await runCompleteHireTool({
    entry_id: opts.candidate.id,
    user_id: provisioned.user_id,
    work_email: opts.workEmail.trim(),
    contract_template_id: templateId,
  });

  let formEmailed = false;
  const formUrl = hireResult.onboarding_form_id
    ? `/me/assigned-form?entry=${encodeURIComponent(hireResult.onboarding_form_id)}`
    : null;
  if (formUrl) {
    const personalEmail = fieldString(opts.candidate, 'email');
    const displayName =
      (opts.candidate.title || '').trim() || personalEmail || undefined;
    const prompt = await setOnboardingPrompt(
      opts.workspaceId,
      provisioned.user_id,
      formUrl,
      { recipientEmail: personalEmail, recipientName: displayName },
    );
    formEmailed = prompt.emailed;
  }

  return {
    employeeId: hireResult.employee_id,
    credentialsEmailed: provisioned.credentials_emailed,
    formEmailed,
    formUrl,
  };
}
