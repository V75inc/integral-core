import { useAui, useAuiState } from "@assistant-ui/react";
import { ArrowUpIcon } from "lucide-react";

import { useChatEntityRefsOptional } from "../../../context/ChatEntityRefsContext";
import { useComposerDictationActions } from "../../speech/ComposerDictationContext";

/** Send after dictation settles and entity refs are flushed to the outgoing turn. */
export function ComposerSendWithRefs({
  className,
  "aria-label": ariaLabel = "Send message",
}: {
  className?: string;
  "aria-label"?: string;
}) {
  const aui = useAui();
  const canSend = useAuiState((s) => s.composer.canSend);
  const entityRefsCtx = useChatEntityRefsOptional();
  const dictation = useComposerDictationActions();

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      className={className}
      disabled={!canSend}
      onClick={() => {
        if (dictation) {
          dictation.submit();
          return;
        }
        entityRefsCtx?.flushPendingEntityRefs();
        aui.composer().send();
      }}
    >
      <ArrowUpIcon size={16} strokeWidth={2.5} />
    </button>
  );
}
