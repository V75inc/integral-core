import { useCallback } from "react";
import { PhoneOff } from "lucide-react";
import { getMountedConfirm, useConfirmOptional } from "../../context/ConfirmContext";
import { peekVoiceSession, type VoiceSession } from "./nativeVoiceSession";

type Predicate = (session: VoiceSession) => boolean;
const anyCall: Predicate = () => true;

async function confirmVoiceDeparture(confirm: ReturnType<typeof useConfirmOptional>, predicate: Predicate = anyCall) {
  const session = peekVoiceSession();
  if (!session || !predicate(session)) return true;
  if (!confirm || !await confirm({
    title: "End voice call?",
    message: "Leaving this conversation will end your live call. Accepted work will continue in chat.",
    cancelLabel: "Stay in call",
    confirmLabel: "End call and leave",
    variant: "danger",
    titleIcon: <PhoneOff size={14} />,
  })) return false;
  const latest = peekVoiceSession();
  if (latest && latest.owner !== session.owner) return false;
  latest?.close();
  return true;
}

/** For app-owned document actions, including the system Reload notification. */
export function guardAppDeparture(action: () => void) {
  if (!peekVoiceSession()) { action(); return; }
  void confirmVoiceDeparture(getMountedConfirm()).then(approved => { if (approved) action(); });
}

export function useVoiceDepartureGuard() {
  const confirm = useConfirmOptional();
  const confirmDeparture = useCallback((predicate: Predicate = anyCall) =>
    confirmVoiceDeparture(confirm, predicate), [confirm]);
  const guardDeparture = useCallback((action: () => void, predicate: Predicate = anyCall) => {
    const session = peekVoiceSession();
    if (!session || !predicate(session)) { action(); return; }
    void confirmDeparture(predicate).then(approved => { if (approved) action(); });
  }, [confirmDeparture]);
  return { confirmDeparture, guardDeparture };
}
