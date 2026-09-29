import { requestOpenCompanionChat } from '../features/ai-chat/chatHandoff';
import { Button } from './ui';

export const IMPROVE_THIS_EVENT = 'integral:improve-this';

/** Open the companion chat with "Improve this" for the focused object. */
export function ImproveThisButton() {
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => {
        requestOpenCompanionChat();
        window.dispatchEvent(new CustomEvent(IMPROVE_THIS_EVENT));
      }}
    >
      Improve this
    </Button>
  );
}
