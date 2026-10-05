/**
 * Taggable composer wired to assistant-ui thread composer state.
 */
import { useAui, useAuiState } from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { TaggableComposer } from "../../../components/chat/TaggableComposer";
import { useChatEntityRefsOptional } from "../../../context/ChatEntityRefsContext";
import type { ChatEntityRef } from "../../../types/chatEntityRefs";
import { useChatActivity } from "../AIChatSurface";
import { useComposerDictationActions } from "../../speech/ComposerDictationContext";
import { consumePendingChatDraft, OPEN_AI_CHAT_EVENT } from "../chatHandoff";

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
  const { activeThreadId } = useChatActivity();
  const composerText = useAuiState((s) => s.composer.text ?? "");
  const [localValue, setLocalValue] = useState(composerText);
  // The composer is one box for the whole surface. Switching chats (including
  // New conversation, and the thread created by the first send) must drop the
  // previous draft, or the next message is glued onto it.
  const seenThread = useRef(activeThreadId);
  const [entityRefs, setEntityRefs] = useState<ChatEntityRef[]>([]);
  const pendingDraftRef = useRef<{ text: string; threadId: string | null } | null>(null);
  const pendingDraftTimerRef = useRef<number | null>(null);
  const submitAfterDictationRef = useRef(false);
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
    if (seenThread.current === activeThreadId) return;
    seenThread.current = activeThreadId;
    setLocalValue("");
    setEntityRefs([]);
    entityRefsCtx?.setPendingEntityRefs([]);
    aui.composer().setText("");
    const pendingDraft = pendingDraftRef.current;
    if (pendingDraft && pendingDraft.threadId !== activeThreadId) {
      setLocalValue(pendingDraft.text);
      aui.composer().setText(pendingDraft.text);
      pendingDraftRef.current = null;
      if (pendingDraftTimerRef.current != null) {
        window.clearTimeout(pendingDraftTimerRef.current);
        pendingDraftTimerRef.current = null;
      }
    }
  }, [activeThreadId, aui, entityRefsCtx]);

  useEffect(() => {
    const applyPendingDraft = () => {
      const text = consumePendingChatDraft();
      if (!text) return;
      pendingDraftRef.current = { text, threadId: activeThreadId };
      setLocalValue(text);
      aui.composer().setText(text);
      if (pendingDraftTimerRef.current != null) {
        window.clearTimeout(pendingDraftTimerRef.current);
      }
      pendingDraftTimerRef.current = window.setTimeout(() => {
        pendingDraftRef.current = null;
        pendingDraftTimerRef.current = null;
      }, 1000);
    };
    applyPendingDraft();
    window.addEventListener(OPEN_AI_CHAT_EVENT, applyPendingDraft);
    return () => {
      window.removeEventListener(OPEN_AI_CHAT_EVENT, applyPendingDraft);
      if (pendingDraftTimerRef.current != null) {
        window.clearTimeout(pendingDraftTimerRef.current);
      }
    };
  }, [activeThreadId, aui]);

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
      if (submitAfterDictationRef.current) return;
      submitAfterDictationRef.current = true;
      void dictation
        .stop("submit")
        .then(sendNow)
        .finally(() => {
          submitAfterDictationRef.current = false;
        });
      return;
    }
    sendNow();
  }, [dictation, sendNow]);

  useEffect(() => {
    dictation?.registerSubmit(handleSubmit);
    return () => dictation?.registerSubmit(null);
  }, [dictation, handleSubmit]);

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
