import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useScope } from '../../context/ScopeContext';
import { appsApi } from '../../api/apps';
import { KanbanHireModal } from './KanbanHireModal';
import {
  subscribeKanbanHirePrompt,
  type KanbanHirePrompt,
} from './kanbanHirePrompt';
import { resolveHireWorkspaceId } from './kanbanHireApi';

function appMatchesRecruitment(
  app: { workspace_id?: string; name?: string } & Record<string, unknown>,
  workspaceId: string,
): boolean {
  if (app.workspace_id !== workspaceId) return false;
  const pkg = String(app.installed_package_slug || app.package_slug || '')
    .trim()
    .toLowerCase();
  if (pkg === 'recruitment-app') return true;
  return String(app.name || '').trim().toLowerCase() === 'recruitment';
}

export function KanbanHirePromptHost() {
  const { activeWorkspace } = useScope();
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState<KanbanHirePrompt | null>(null);
  const candidate = prompt?.candidate ?? null;
  const [hireWorkspace, setHireWorkspace] = useState<{
    id: string;
    error?: string;
    loading: boolean;
  }>({ id: '', loading: false });

  const [recruitmentAppId, setRecruitmentAppId] = useState<string | undefined>(
    prompt?.appId,
  );

  useEffect(() => subscribeKanbanHirePrompt(next => setPrompt(next)), []);

  useEffect(() => {
    if (!candidate) {
      setHireWorkspace({ id: '', loading: false });
      return;
    }
    let cancelled = false;
    setHireWorkspace(prev => ({ ...prev, loading: true }));
    void resolveHireWorkspaceId({
      candidate,
      hintWorkspaceId: prompt?.workspaceId,
      recruitmentAppId: prompt?.appId || recruitmentAppId,
      scopeWorkspace: activeWorkspace,
    }).then(res => {
      if (cancelled) return;
      setHireWorkspace({
        id: res.workspaceId,
        error: res.error,
        loading: false,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [candidate, prompt?.workspaceId, prompt?.appId, recruitmentAppId, activeWorkspace]);

  useEffect(() => {
    const workspaceId = hireWorkspace.id;
    if (!workspaceId) return;
    let cancelled = false;
    void appsApi.list().then(apps => {
      if (cancelled) return;
      const match = apps.find(a =>
        appMatchesRecruitment(a as typeof a & Record<string, unknown>, workspaceId),
      );
      setRecruitmentAppId(prompt?.appId || match?.id);
    });
    return () => {
      cancelled = true;
    };
  }, [hireWorkspace.id, prompt?.appId]);

  return (
    <KanbanHireModal
      open={Boolean(candidate)}
      intent={prompt?.intent ?? 'complete_hire'}
      candidate={candidate}
      workspaceId={hireWorkspace.id}
      workspaceBlockReason={
        hireWorkspace.loading
          ? 'Resolving organization workspace…'
          : hireWorkspace.error
      }
      recruitmentAppId={recruitmentAppId}
      onClose={() => setPrompt(null)}
      onCompleted={() => {
        void queryClient.invalidateQueries();
      }}
    />
  );
}
