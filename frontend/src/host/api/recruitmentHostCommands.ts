/** Business-owned account commands run outside transactional App operations.
 * The sandbox keeps the operation bridge; credentials stay in the authenticated
 * parent. The server rechecks App, candidate, workspace and member authority.
 */
import apiClient, { getActiveScopeHeader } from '../../api/client';

import { mapRecruitmentHostCommand } from './recruitmentHostCommandMap';

let registered = false;
export function registerRecruitmentHostCommands(): void {
  if (registered) return;
  registered = true;
  apiClient.interceptors.request.use(config => {
    if (
      config.method?.toLowerCase() !== 'post' ||
      !/^\/extensions\/[^/]+\/operations\/recruitment_hire_wizard_(provision_member|assigned_form_prompt)$/.test(
        config.url || '',
      )
    ) {
      return config;
    }
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    const mapped = mapRecruitmentHostCommand(config.url || '', body?.input || {}, getActiveScopeHeader());
    if (!mapped) return config;
    config.url = mapped.url;
    config.method = mapped.method;
    config.data = mapped.data;
    return config;
  });
}
