import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../ui';
import { Surface, Text } from '../../ui';
import { workItemsApi, type WorkItemStatusResponse } from '../../api/workItems';
import { formatRelativeTime } from '../../utils';

const terminal = new Set(['succeeded', 'failed', 'cancelled', 'expired', 'dead_letter']);
const operationLabels: Record<string, string> = { install: 'App install', upgrade: 'App update', uninstall: 'App uninstall', resume: 'App resume' };
const statusLabels: Record<string, string> = {
  queued: 'Queued', running: 'Running', waiting_for_human: 'Awaiting your input',
  waiting_for_event: 'Waiting for an event', retry_wait: 'Waiting to retry',
  succeeded: 'Completed', failed: 'Failed', cancelled: 'Cancelled', expired: 'Expired',
  dead_letter: 'Needs attention',
};

/** Workspace-keyed by the parent; superseded requests never publish old state. */
export function WorkItemsSection({ workspaceId }: { workspaceId: string }) {
  const [items, setItems] = useState<WorkItemStatusResponse[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const load = useCallback(async (after?: string) => {
    const current = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const result = await workItemsApi.list(workspaceId, after);
      if (current !== generation.current) return;
      setItems(previous => after ? [...previous, ...result.items.filter(item =>
        !previous.some(old => old.work_item_id === item.work_item_id))] : result.items);
      setCursor(result.next_cursor);
    } catch (err) {
      if (current === generation.current)
        setError(err instanceof Error ? err.message : 'Could not load work status');
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [workspaceId]);
  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [load]);
  useEffect(() => {
    if (!items.some(item => !terminal.has(item.status))) return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [items, load]);

  return <section aria-label="Workspace work">
    <div className="mb-3 flex items-center justify-between gap-3">
      <Text as="h2" weight="medium">Workspace work</Text>
      <Button size="sm" variant="ghost" disabled={loading} onClick={() => void load()}>Refresh work</Button>
    </div>
    <Text as="p" tone="subtle" variant="meta">Recent work in this workspace, including App updates and uninstalls. Queued work is not complete.</Text>
    {error && <div role="alert"><Text tone="danger">{error}</Text></div>}
    {loading && <div role="status"><Text variant="meta">Loading work status…</Text></div>}
    {!loading && !error && items.length === 0 && <Text as="p" tone="subtle">No workspace work yet.</Text>}
    <div className="mt-3 space-y-3">
      {items.map(item => <Surface key={item.work_item_id} tone="panel" border="subtle" radius="card" className="min-w-0 space-y-3 p-4">
        <Text as="h3" weight="medium">{item.kind === 'app_lifecycle' ? (operationLabels[item.operation ?? ''] ?? 'App lifecycle') : item.kind.replace(/_/g, ' ')} · {statusLabels[item.status] ?? item.status}</Text>
        <Text as="p" variant="meta" tone="subtle">Updated {formatRelativeTime(item.updated_at)} · Attempt {item.attempt}</Text>
        <dl className="space-y-2">
          <div className="space-y-1">
            <Text as="dt" variant="label" tone="subtle">Work ID</Text>
            <Text as="dd" variant="mono" truncate title={item.work_item_id} className="min-w-0">{item.work_item_id}</Text>
          </div>
          {item.app_id && <div className="space-y-1">
            <Text as="dt" variant="label" tone="subtle">App ID</Text>
            <Text as="dd" variant="mono" truncate title={item.app_id} className="min-w-0">{item.app_id}</Text>
          </div>}
        </dl>
        {item.failure && <Text as="p" tone="danger">{item.failure.message || item.failure.code}</Text>}
        {item.next_attempt_at && item.status === 'retry_wait' && <Text as="p" variant="meta">Next attempt: {item.next_attempt_at}</Text>}
      </Surface>)}
    </div>
    {cursor && <Button className="mt-3" disabled={loading} onClick={() => void load(cursor)}>Load older work</Button>}
  </section>;
}
