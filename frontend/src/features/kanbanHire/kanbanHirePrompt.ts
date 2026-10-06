import type { Entry } from '../../types';

export interface KanbanHireStageChangeContext {
  appId?: string;
  workspaceId?: string;
}

/**
 * Hire-stage kanban gate. Returns true when the stage change was intercepted
 * (caller should revert optimistic state). Stub returns false until the hire
 * prompt surface lands.
 */
export function interceptKanbanHireStageChange(
  _previous: Entry,
  _next: Entry,
  _ctx: KanbanHireStageChangeContext,
): boolean {
  return false;
}
