import { ComposerPrimitive } from "@assistant-ui/react";
import { ArrowUpIcon } from "lucide-react";

import { useChatEntityRefsOptional } from "../../../context/ChatEntityRefsContext";

/**
 * Send button — flush entity refs before assistant-ui clears the composer.
 */
export function ComposerSendWithRefs({
  className,
  "aria-label": ariaLabel = "Send message",
}: {
  className?: string;
  "aria-label"?: string;
}) {
  const entityRefsCtx = useChatEntityRefsOptional();

  return (
    <ComposerPrimitive.Send
      aria-label={ariaLabel}
      className={className}
      onMouseDown={() => {
        entityRefsCtx?.flushPendingEntityRefs();
      }}
    >
      <ArrowUpIcon size={16} strokeWidth={2.5} />
    </ComposerPrimitive.Send>
  );
}
