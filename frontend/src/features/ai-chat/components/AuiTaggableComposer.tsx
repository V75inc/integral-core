/**
 * Taggable composer wired to assistant-ui thread composer state.
 */
import { useAui, useAuiState } from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { TaggableComposer } from "../../../components/chat/TaggableComposer";
import { useChatEntityRefsOptional } from "../../../context/ChatEntityRefsContext";
import type { ChatEntityRef } from "../../../types/chatEntityRefs";
import { useComposerDictationActions } from "../../speech/ComposerDictationContext";

type AuiTaggableComposerProps = Omit<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "value" | "onChange"
>;

export function AuiTaggableComposer({
  className,
  style,
  placeholder,
  disabled,
  rows,
  autoFocus,
  "aria-label": ariaLabel,
}: AuiTaggableComposerProps) {
  const aui = useAui();
  const composerText = useAuiState((s) => s.composer.text ?? "");
  const [localValue, setLocalValue] = useState(composerText);
  const [entityRefs, setEntityRefs] = useState<ChatEntityRef[]>([]);
  const entityRefsCtx = useChatEntityRefsOptional();
  const dictation = useComposerDictationActions();
  // Read at send time: a send that waits for dictation to finish must see the
  // refs as they are after the last dictated words, not when Enter was hit.
  const entityRefsRef = useRef(entityRefs);
  entityRefsRef.current = entityRefs;

  useEffect(() => {
    setLocalValue(composerText);
  }, [composerText]);

  useEffect(() => {
    entityRefsCtx?.registerComposerEntityRefsReset(() => setEntityRefs([]));
    return () => entityRefsCtx?.registerComposerEntityRefsReset(null);
  }, [entityRefsCtx]);

  const handleChange = useCallback(
    (next: string) => {
      setLocalValue(next);
      aui.composer().setText(next);
    },
    [aui],
  );

  const handleEntityRefsChange = useCallback(
    (refs: ChatEntityRef[]) => {
      setEntityRefs(refs);
      entityRefsCtx?.setPendingEntityRefs(refs);
    },
    [entityRefsCtx],
  );

  const sendNow = useCallback(() => {
    entityRefsCtx?.setPendingEntityRefs(entityRefsRef.current);
    entityRefsCtx?.flushPendingEntityRefs();
    void aui.composer().send();
  }, [aui, entityRefsCtx]);

  const handleSubmit = useCallback(() => {
    // Finish dictation first so the last words land before the message goes.
    // Stopping for a submit never auto-sends, so this sends exactly once.
    if (dictation?.isListening()) {
      void dictation.stop("submit").then(sendNow);
      return;
    }
    sendNow();
  }, [dictation, sendNow]);

  useEffect(() => {
    dictation?.registerSubmit(sendNow);
    return () => dictation?.registerSubmit(null);
  }, [dictation, sendNow]);

  return (
    <TaggableComposer
      ref={dictation?.registerTextarea}
      value={localValue}
      onChange={handleChange}
      entityRefs={entityRefs}
      onEntityRefsChange={handleEntityRefsChange}
      onSubmit={handleSubmit}
      className={className}
      style={style}
      placeholder={placeholder}
      disabled={disabled}
      rows={rows}
      autoFocus={autoFocus}
      aria-label={ariaLabel}
    />
  );
}
