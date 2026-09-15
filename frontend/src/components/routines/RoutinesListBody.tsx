import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ListTodo,
  Pause,
  Play,
  Pencil,
  X,
  MessageSquare,
  History,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from 'lucide-react';
import { EmptyState, IconWell, LINE_ICON_STROKE } from '../ui';
import { Input, Textarea, Select } from '../../ui';
import { Field } from '../../patterns';
import { FormDialog } from '../../templates';
import { formatRelativeTime } from '../../utils';
import {
  buildCronFromPreset,
  describeCron,
  detectSchedulePreset,
  parseCronTime,
  parseCronWeekday,
  SCHEDULE_PRESET_OPTIONS,
  type SchedulePresetId,
} from '../../utils/scheduleCron';
import {
  cancelRoutine,
  getRoutineActivity,
  updateRoutine,
  type RoutineActivityEvent,
  type RoutineActivityLink,
  type RoutineActivityResponse,
  type RoutineResponse,
  type RoutineUpdateRequest,
} from '../../api/routines';

interface RoutinesListBodyProps {
  rows: RoutineResponse[];
  loading: boolean;
  error: string | null;
  onRetry(): void;
  onChanged?(): void;
}

export function RoutinesListBody({
  rows,
  loading,
  error,
  onRetry,
  onChanged,
}: RoutinesListBodyProps) {
  if (error) {
    return (
      <div
        className="rounded-[var(--radius-card)] border border-[color:var(--danger-fg)]/30 bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-fg)]"
        role="alert"
      >
        {error}
        <button type="button" className="ml-3 underline" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4, 5].map(i => (
          <div
            key={i}
            className="h-20 bg-[var(--panel-2)] rounded-lg animate-pulse"
          />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--panel-border)] bg-[var(--panel)]">
        <EmptyState
          icon={
            <IconWell size="lg" aria-hidden>
              <ListTodo size={22} strokeWidth={LINE_ICON_STROKE} />
            </IconWell>
          }
          title="No background tasks"
          description="Scheduled routines from Agent conversations appear here. Ask the agent to schedule recurring work to get started."
        />
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {rows.map(routine => (
        <RoutineRow
          key={routine.id}
          routine={routine}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

function statusDotClass(status: string): string {
  switch (status) {
    case 'active':
      return 'bg-[var(--success-fg)]';
    case 'paused':
      return 'bg-[var(--warn-fg)]';
    case 'completed':
      return 'bg-[var(--panel-border)]';
    case 'cancelled':
      return 'bg-[var(--danger-fg)]';
    default:
      return 'bg-[var(--panel-border)]';
  }
}

function RoutineRow({
  routine,
  onChanged,
}: {
  routine: RoutineResponse;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);

  const isTerminal =
    routine.status === 'cancelled' || routine.status === 'completed';
  const canPause = routine.status === 'active';
  const canResume =
    routine.status === 'paused' || routine.status === 'completed';
  const canEdit = routine.status !== 'cancelled';
  const canCancel = routine.status !== 'cancelled';
  const scheduleLabel = describeCron(routine.cron, routine.timezone);
  const runMeta =
    routine.max_runs != null
      ? `${routine.run_count}/${routine.max_runs} successful runs`
      : routine.run_count > 0
        ? `${routine.run_count} successful runs`
        : null;
  const lastFailed = routine.last_run_status === 'error';

  const handlePauseResume = async () => {
    if (busy) return;
    const next = canPause ? 'paused' : 'active';
    setBusy(true);
    setRowError(null);
    try {
      await updateRoutine(routine.id, { status: next });
      onChanged?.();
    } catch (err) {
      setRowError(
        err instanceof Error ? err.message : 'Failed to update status',
      );
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (busy || !canCancel) return;
    const ok = window.confirm(
      'Cancel this background task? It will stop running on its schedule.',
    );
    if (!ok) return;
    setBusy(true);
    setRowError(null);
    try {
      await cancelRoutine(routine.id);
      onChanged?.();
    } catch (err) {
      setRowError(
        err instanceof Error ? err.message : 'Failed to cancel task',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="rounded-lg border border-[var(--panel-border)] bg-[var(--panel)]">
        <div className="flex items-start gap-4 p-4">
          <div
            className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${statusDotClass(routine.status)}`}
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[var(--text)] line-clamp-2">
              {routine.instruction}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--text-muted)]">
              <span className="capitalize">{routine.status}</span>
              <span aria-hidden>·</span>
              <span title={routine.cron}>{scheduleLabel}</span>
              {routine.next_run_at && routine.status === 'active' && (
                <>
                  <span aria-hidden>·</span>
                  <span>next {formatRelativeTime(routine.next_run_at)}</span>
                </>
              )}
              {routine.last_run_at && (
                <>
                  <span aria-hidden>·</span>
                  <span
                    className={
                      lastFailed ? 'text-[var(--danger-fg)]' : undefined
                    }
                  >
                    last {formatRelativeTime(routine.last_run_at)}
                    {lastFailed
                      ? ' — failed'
                      : routine.last_run_status
                        ? ` (${routine.last_run_status})`
                        : ''}
                  </span>
                </>
              )}
              {runMeta && (
                <>
                  <span aria-hidden>·</span>
                  <span>{runMeta}</span>
                </>
              )}
            </p>
            {lastFailed && routine.last_run_error && (
              <p className="mt-1.5 text-[11px] text-[var(--danger-fg)] line-clamp-2">
                {routine.last_run_error}
              </p>
            )}
            {rowError && (
              <div className="mt-2 rounded-[var(--radius-input)] border border-[color:var(--danger-fg)]/20 bg-[var(--danger-bg)] px-2 py-1 text-[11px] text-[var(--danger-fg)]">
                {rowError}
              </div>
            )}
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {routine.thread_id ? (
                <Link
                  to={`/agent?thread=${encodeURIComponent(routine.thread_id)}`}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--link)] hover:bg-[var(--panel-2)] hover:text-[var(--link-hover)]"
                >
                  <MessageSquare size={12} strokeWidth={LINE_ICON_STROKE} />
                  Open conversation
                </Link>
              ) : null}
              <button
                type="button"
                onClick={() => setActivityOpen(v => !v)}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
              >
                <History size={12} strokeWidth={LINE_ICON_STROKE} />
                Activity
                {activityOpen ? (
                  <ChevronUp size={12} strokeWidth={LINE_ICON_STROKE} />
                ) : (
                  <ChevronDown size={12} strokeWidth={LINE_ICON_STROKE} />
                )}
              </button>
            </div>
          </div>
          {!isTerminal || canResume ? (
            <div className="flex shrink-0 items-center gap-1">
              {canEdit && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditOpen(true)}
                  aria-label="Edit task"
                  className="rounded-lg p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Pencil size={14} strokeWidth={LINE_ICON_STROKE} />
                </button>
              )}
              {(canPause || canResume) && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handlePauseResume()}
                  aria-label={canPause ? 'Pause task' : 'Resume task'}
                  className="rounded-lg p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {canPause ? (
                    <Pause size={14} strokeWidth={LINE_ICON_STROKE} />
                  ) : (
                    <Play size={14} strokeWidth={LINE_ICON_STROKE} />
                  )}
                </button>
              )}
              {canCancel && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleCancel()}
                  aria-label="Cancel task"
                  className="rounded-lg p-1.5 text-[var(--danger-fg)] transition-colors hover:bg-[var(--panel-2)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <X size={14} strokeWidth={LINE_ICON_STROKE} />
                </button>
              )}
            </div>
          ) : null}
        </div>

        {activityOpen && (
          <RoutineActivityPanel routineId={routine.id} />
        )}
      </div>

      {editOpen && (
        <EditRoutineDialog
          routine={routine}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            onChanged?.();
          }}
        />
      )}
    </>
  );
}

function RoutineActivityPanel({ routineId }: { routineId: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<RoutineActivityResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void getRoutineActivity(routineId)
      .then(payload => {
        if (!cancelled) setData(payload);
      })
      .catch(err => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load activity',
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [routineId]);

  return (
    <div className="border-t border-[var(--panel-border)] bg-[var(--panel-2)]/40 px-4 py-3">
      {loading && (
        <p className="text-xs text-[var(--text-subtle)]">Loading activity…</p>
      )}
      {error && (
        <p className="text-xs text-[var(--danger-fg)]" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && data && (
        <div className="space-y-3">
          {data.write_scope_links.length > 0 && (
            <div>
              <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-[var(--text-subtle)]">
                Allowed targets
              </p>
              <div className="flex flex-wrap gap-1.5">
                {data.write_scope_links.map(link => (
                  <ActivityLinkChip key={`${link.kind}:${link.id}`} link={link} />
                ))}
              </div>
            </div>
          )}
          {data.events.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">
              No run history yet. After the next scheduled run, results and
              conversation turns will show up here.
            </p>
          ) : (
            <ul className="space-y-2">
              {data.events.map(ev => (
                <ActivityEventRow key={ev.id} event={ev} />
              ))}
            </ul>
          )}
          {data.thread_id ? (
            <Link
              to={`/agent?thread=${encodeURIComponent(data.thread_id)}`}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-[var(--link)] hover:text-[var(--link-hover)]"
            >
              View full conversation
              <ExternalLink size={11} strokeWidth={LINE_ICON_STROKE} />
            </Link>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ActivityEventRow({ event }: { event: RoutineActivityEvent }) {
  const isError =
    event.status === 'error' ||
    event.action === 'routine_task.auto_paused' ||
    (event.summary || '').toLowerCase().includes('failed');
  return (
    <li className="rounded-md border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p
          className={`text-xs ${
            isError
              ? 'text-[var(--danger-fg)]'
              : 'text-[var(--text)]'
          }`}
        >
          {event.summary}
        </p>
        {event.ts && (
          <span className="shrink-0 text-[10px] text-[var(--text-subtle)]">
            {formatRelativeTime(event.ts)}
          </span>
        )}
      </div>
      {event.kind === 'message' && event.role && (
        <p className="mt-0.5 text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">
          {event.role} message
        </p>
      )}
      {event.links.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {event.links.map(link => (
            <ActivityLinkChip key={`${link.href}:${link.id}`} link={link} />
          ))}
        </div>
      )}
    </li>
  );
}

function ActivityLinkChip({ link }: { link: RoutineActivityLink }) {
  return (
    <Link
      to={link.href}
      className="inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-0.5 text-[11px] text-[var(--link)] hover:border-[var(--link)] hover:text-[var(--link-hover)]"
    >
      <span className="capitalize text-[var(--text-subtle)]">{link.kind}</span>
      <span className="max-w-[12rem] truncate">{link.label}</span>
    </Link>
  );
}

function EditRoutineDialog({
  routine,
  open,
  onClose,
  onSaved,
}: {
  routine: RoutineResponse;
  open: boolean;
  onClose(): void;
  onSaved(): void;
}) {
  const [instruction, setInstruction] = useState(routine.instruction);
  const initialPreset = detectSchedulePreset(routine.cron);
  const initialTime = parseCronTime(routine.cron);
  const [preset, setPreset] = useState<SchedulePresetId>(initialPreset);
  const [hour, setHour] = useState(initialTime.hour);
  const [minute, setMinute] = useState(initialTime.minute);
  const [weekday, setWeekday] = useState(parseCronWeekday(routine.cron));
  const [customCron, setCustomCron] = useState(routine.cron);
  const [maxRuns, setMaxRuns] = useState(
    routine.max_runs != null ? String(routine.max_runs) : '',
  );
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const resolvedCron = buildCronFromPreset(preset, {
    hour,
    minute,
    weekday,
    customCron,
  });
  const needsTime = preset === 'daily' || preset === 'weekdays' || preset === 'weekly';

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const body: RoutineUpdateRequest = {
        instruction: instruction.trim(),
        cron: resolvedCron,
      };
      const trimmedMax = maxRuns.trim();
      if (trimmedMax === '') {
        if (routine.max_runs != null) {
          body.clear_max_runs = true;
        }
      } else {
        const n = Number.parseInt(trimmedMax, 10);
        if (!Number.isFinite(n) || n <= 0) {
          setFormError('Max runs must be a positive integer (or empty).');
          setSubmitting(false);
          return;
        }
        body.max_runs = n;
      }
      await updateRoutine(routine.id, body);
      onSaved();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : 'Failed to save changes',
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title="Edit background task"
      onSubmit={e => void handleSubmit(e)}
      submitLabel="Save"
      submitLoading={submitting}
      submitDisabled={!instruction.trim() || !resolvedCron.trim()}
      size="form"
    >
      <div className="flex flex-col gap-4">
        <Field label="Instruction" required>
          <Textarea
            value={instruction}
            onChange={e => setInstruction(e.target.value)}
            rows={3}
          />
        </Field>

        <Field
          label="Schedule"
          required
          hint={describeCron(resolvedCron, routine.timezone)}
        >
          <Select
            value={preset}
            onChange={e => setPreset(e.target.value as SchedulePresetId)}
          >
            {SCHEDULE_PRESET_OPTIONS.map(opt => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </Select>
        </Field>

        {needsTime && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Hour (0–23)">
              <Input
                type="number"
                min={0}
                max={23}
                value={hour}
                onChange={e => setHour(Number(e.target.value) || 0)}
              />
            </Field>
            <Field label="Minute (0–59)">
              <Input
                type="number"
                min={0}
                max={59}
                value={minute}
                onChange={e => setMinute(Number(e.target.value) || 0)}
              />
            </Field>
          </div>
        )}

        {preset === 'weekly' && (
          <Field label="Day of week">
            <Select
              value={String(weekday)}
              onChange={e => setWeekday(Number(e.target.value))}
            >
              <option value="0">Sunday</option>
              <option value="1">Monday</option>
              <option value="2">Tuesday</option>
              <option value="3">Wednesday</option>
              <option value="4">Thursday</option>
              <option value="5">Friday</option>
              <option value="6">Saturday</option>
            </Select>
          </Field>
        )}

        {preset === 'custom' && (
          <Field
            label="Cron expression"
            hint="Advanced 5-field cron (minute hour day month weekday)."
          >
            <Input
              value={customCron}
              onChange={e => setCustomCron(e.target.value)}
              placeholder="0 9 * * *"
              monospace
            />
          </Field>
        )}

        <Field
          label="Max runs"
          hint="Leave empty for open-ended. Caps successful runs only."
        >
          <Input
            type="number"
            min={1}
            value={maxRuns}
            onChange={e => setMaxRuns(e.target.value)}
            placeholder="Unlimited"
          />
        </Field>
        {formError && (
          <p className="text-sm text-[var(--danger-fg)]" role="alert">
            {formError}
          </p>
        )}
      </div>
    </FormDialog>
  );
}
