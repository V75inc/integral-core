import { Sparkles } from 'lucide-react';
import { IconButton } from '../../ui';
import { requestOpenCompanionChat } from '../../features/ai-chat/chatHandoff';

export type ImprovementTarget = 'entry' | 'track' | 'view';

/** Open the resident with the already-published page context and a review-only request. */
export function ImproveThisButton({
  target,
  subjectName,
  subjectId,
  trackId,
  disabled = false,
}: {
  target: ImprovementTarget;
  subjectName?: string | null;
  subjectId?: string | null;
  trackId?: string | null;
  disabled?: boolean;
}) {
  const label = target === 'entry' ? 'record' : target;
  const subject = subjectName?.trim();
  const resourceId = subjectId?.trim();
  const parentTrackId = trackId?.trim();
  const targetContext = [
    resourceId ? `Focused ${label} ID: "${resourceId}".` : '',
    parentTrackId && target !== 'track'
      ? `Parent track ID: "${parentTrackId}".`
      : '',
  ]
    .filter(Boolean)
    .join(' ');
  const reviewWorkflow =
    target === 'entry'
      ? 'Use the integral_model skill to inspect the focused entry and its parent track’s existing model before proposing any evidence-based revision. Do not create a new model.'
      : `Use the integral_model skill. Call integral_describe_model for the focused track${parentTrackId ? ` with track_id="${parentTrackId}"` : ''}, then call integral_recommend_customizations for that same track before proposing changes. If there is an evidence-backed recommendation, open a draft for the attached model, stage one revision using only that suggestion, and show me its diff. Do not call integral_draft_new_model or integral_author_model; this target already has an attached model. If there is no attached model or no evidence-backed recommendation, report that and stop without creating a new model.`;
  return (
    <IconButton
      type="button"
      size="md"
      label={`Improve this ${label}`}
      title={`Improve this ${label}`}
      onClick={() =>
        requestOpenCompanionChat({
          draftText:
            `Please review the currently open ${label}${subject ? ` “${subject}”` : ''} ` +
            `${targetContext ? `${targetContext} ` : ''}` +
            `Review its existing schema and available records. ${reviewWorkflow} ` +
            'Show me the diff. ' +
            'Do not publish the revision until I approve it.',
        })
      }
      disabled={disabled}
    >
      <Sparkles size={16} aria-hidden />
    </IconButton>
  );
}
