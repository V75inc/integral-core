import { useEffect, useRef } from "react";
import { matchRoutes, useBlocker, type RouteObject, type Location } from "react-router-dom";
import { peekVoiceSession } from "./nativeVoiceSession";
import { useVoiceDepartureGuard } from "./useVoiceDepartureGuard";

export function abandonsVoiceConversation(routes: RouteObject[], current: Location, next: Location) {
  const voice = peekVoiceSession();
  if (!voice || (current.pathname === next.pathname && current.search === next.search)) return false;
  if (!matchRoutes(routes, next)?.some(match => match.route.id === "workspace-shell")) return true;
  if (voice.surface === "home") return current.pathname !== next.pathname;
  const targetThread = next.pathname === "/agent" && new URLSearchParams(next.search).get("thread");
  if (targetThread && targetThread !== voice.threadId) return true;
  const workspace = matchRoutes(routes, next)?.at(-1)?.params.workspaceId;
  return Boolean(workspace && next.pathname.startsWith("/workspaces/") && workspace !== voice.workspaceId);
}

// The data router also covers same-document back/forward. A native document
// departure cannot display our modal; the session registry owns beforeunload.
export function VoiceRouteGuard({ routes }: { routes: RouteObject[] }) {
  const { confirmDeparture } = useVoiceDepartureGuard();
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    abandonsVoiceConversation(routes, currentLocation, nextLocation));
  const pending = useRef(false);
  useEffect(() => {
    if (blocker.state !== "blocked" || pending.current) return;
    pending.current = true;
    void confirmDeparture().then(approved => {
      pending.current = false;
      if (approved) blocker.proceed();
      else blocker.reset();
    });
  }, [blocker, confirmDeparture]);
  return null;
}
