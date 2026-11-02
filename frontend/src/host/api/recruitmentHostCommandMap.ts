/** Fixed Business command routes; never accept a URL from sandbox content. */
const commands = new Set([
  'recruitment_hire_wizard_provision_member',
  'recruitment_hire_wizard_assigned_form_prompt',
]);

export function mapRecruitmentHostCommand(
  url: string, input: Record<string, unknown>, scope: string | null,
): { url: string; method: 'post' | 'get'; data?: Record<string, unknown> } | null {
  const match = /^\/extensions\/([^/]+)\/operations\/([^/]+)$/.exec(url);
  if (!match || !commands.has(match[2])) return null;
  if (!scope?.startsWith('ws:') || !scope.slice(3)) throw new Error('Choose a workspace before managing hire accounts.');
  const workspace = encodeURIComponent(scope.slice(3));
  const app = encodeURIComponent(decodeURIComponent(match[1]));
  if (match[2] === 'recruitment_hire_wizard_provision_member') {
    const credentialsEmail =
      typeof input.credentials_email === 'string' && input.credentials_email.trim()
        ? input.credentials_email.trim()
        : undefined;
    return { url: `/workspaces/${workspace}/apps/${app}/recruitment/provision-account`, method: 'post', data: {
      entry_id: input.entry_id, work_email: input.work_email,
      display_name: input.display_name,
      ...(credentialsEmail ? { credentials_email: credentialsEmail } : {}),
    } };
  }
  return {
    url: `/workspaces/${workspace}/apps/${app}/recruitment/member-actions`,
    method: 'post',
    data: {
      entry_id: input.entry_id,
      action: 'prompt',
      form_url: input.form_url,
      send_email: input.send_email,
    },
  };
}
