import type { Entry } from '../../types';

export type KanbanHireIntent = 'offer' | 'complete_hire';

export type KanbanHirePrompt = {
  candidate: Entry;
  intent: KanbanHireIntent;
  appId?: string;
  workspaceId?: string;
};

type Listener = (prompt: KanbanHirePrompt) => void;

const listeners = new Set<Listener>();

function fieldString(entry: Entry, key: string): string {
  const raw = entry.custom_fields?.[key];
  if (raw == null) return '';
  return String(raw).trim();
}

function slugify(value: string): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '');
}

export function isCandidateEntry(entry: Entry): boolean {
  return slugify(entry.type || '') === 'candidate';
}

export function subscribeKanbanHirePrompt(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function requestKanbanHirePrompt(prompt: KanbanHirePrompt): void {
  listeners.forEach(listener => listener(prompt));
}

/** User is moving a candidate into the Offer column. */
export function isKanbanOfferStageTransition(before: Entry, proposed: Entry): boolean {
  if (!isCandidateEntry(before) && !isCandidateEntry(proposed)) return false;
  const beforeStage = fieldString(before, 'stage').toLowerCase();
  const afterStage = fieldString(proposed, 'stage').toLowerCase();
  return afterStage === 'offer' && beforeStage !== 'offer';
}

/** User is moving a candidate into the Hired column (complete hire flow). */
export function isKanbanHireStageTransition(before: Entry, proposed: Entry): boolean {
  if (!isCandidateEntry(before) && !isCandidateEntry(proposed)) return false;
  const beforeStage = fieldString(before, 'stage').toLowerCase();
  const afterStage = fieldString(proposed, 'stage').toLowerCase();
  return afterStage === 'hired' && beforeStage !== 'hired';
}

export interface KanbanHireStageChangeContext {
  appId?: string;
  workspaceId?: string;
}

/**
 * Open the offer or hire dialog before a stage PATCH. Returns true when the
 * transition was intercepted (caller must skip the save).
 */
export function interceptKanbanHireStageChange(
  before: Entry,
  proposedAfter: Entry,
  extras?: KanbanHireStageChangeContext,
): boolean {
  const toOffer = isKanbanOfferStageTransition(before, proposedAfter);
  const toHired = isKanbanHireStageTransition(before, proposedAfter);
  if (!toOffer && !toHired) return false;

  if (toHired) {
    if (
      fieldString(proposedAfter, 'hired_employee') ||
      fieldString(before, 'hired_employee')
    ) {
      return false;
    }
  }

  const custom_fields = {
    ...(before.custom_fields || {}),
    ...(proposedAfter.custom_fields || {}),
  };
  custom_fields.stage = before.custom_fields?.stage ?? custom_fields.stage;

  requestKanbanHirePrompt({
    candidate: { ...before, ...proposedAfter, custom_fields },
    intent: toOffer ? 'offer' : 'complete_hire',
    appId: extras?.appId ?? proposedAfter.app?.id ?? before.app?.id,
    workspaceId:
      extras?.workspaceId ??
      proposedAfter.track?.workspace_id ??
      before.track?.workspace_id,
  });
  return true;
}
