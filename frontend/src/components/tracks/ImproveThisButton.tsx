import { Sparkles } from 'lucide-react';
import { Button } from '../ui';
import { requestOpenCompanionChat } from '../../features/ai-chat/chatHandoff';

export type ImprovementTarget = 'entry' | 'track' | 'view';

/** Open the resident with the already-published page context and a review-only request. */
export function ImproveThisButton({
  target,
  disabled = false,
}: {
  target: ImprovementTarget;
  disabled?: boolean;
}) {
  const label = target === 'entry' ? 'record' : target;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      icon={<Sparkles size={14} />}
      aria-label={`Improve this ${label}`}
      onClick={() =>
        requestOpenCompanionChat({
          draftText:
            `Please review this ${label} using the current model and available records. ` +
            'Suggest evidence-based improvements, open a draft model revision, and show me its diff. ' +
            'Do not publish the revision until I approve it.',
        })
      }
      disabled={disabled}
    >
      Improve this
    </Button>
  );
}
