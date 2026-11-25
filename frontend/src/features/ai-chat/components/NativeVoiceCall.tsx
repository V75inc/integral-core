import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { agentNavigationPath } from "../agentNavigation";
import { requestOpenCompanionChat } from "../chatHandoff";
import { useQuery } from "@tanstack/react-query";
import { AudioLines, Loader2, Mic, MicOff, Square } from "lucide-react";
import { REALTIME_VOICE_QUERY_KEY, realtimeVoiceConfigurationApi } from "../../../api/aiConfiguration";
import { ALL_WORKSPACE_ASSISTANTS, WORKSPACE_ASSISTANT } from "../../../api/chatAssistantIdentity";
import { mintWsTicket } from "../../../api/wsTicket";
import { Button } from "../../../components/ui/Button";
import { HoverTooltip } from "../../../components/ui/HoverTooltip";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { useComposerDictation, useComposerDictationActions } from "../../speech/ComposerDictationContext";
import { Surface, Text } from "../../../ui";
import { useChatActivity } from "../AIChatSurface";
import { useChatPageContextSnapshot } from "../useChatPageContextSnapshot";
import captureModuleUrl from "./nativeVoiceCapture.js?url&no-inline";

const SAMPLE_RATE = 24000;
export type NativeVoiceState = "idle" | "connecting" | "live";

function announceLocalVoiceFailure(message: string) {
  // The provider may be unreachable. Use an installed device voice only.
  // The accessible status remains available if local speech is unsupported.
  try {
    const synth = window.speechSynthesis;
    if (!synth || typeof SpeechSynthesisUtterance === "undefined") return;
    const voice = synth.getVoices().find(candidate =>
      candidate.localService && candidate.lang.toLowerCase().startsWith("en"));
    if (!voice) return;
    const utterance = new SpeechSynthesisUtterance(message);
    utterance.voice = voice;
    synth.speak(utterance);
  } catch { /* The visible status is still available. */ }
}


export function createVoicePlayback(audio: AudioContext) {
  const sources = new Map<AudioBufferSourceNode, { at: number; samples: number; handle?: number }>();
  let nextTime = 0;
  return {
    enqueue(data: ArrayBuffer, handle?: number) {
      if (data.byteLength === 0 || data.byteLength % 2 !== 0) return;
      const pcm = new DataView(data);
      const buffer = audio.createBuffer(1, data.byteLength / 2, SAMPLE_RATE);
      const samples = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index += 1) {
        samples[index] = pcm.getInt16(index * 2, true) / 32768;
      }
      const source = audio.createBufferSource();
      source.buffer = buffer;
      source.connect(audio.destination);
      source.onended = () => { sources.delete(source); source.disconnect(); };
      const at = Math.max(audio.currentTime + 0.02, nextTime);
      sources.set(source, { at, samples: samples.length, handle });
      nextTime = at + buffer.duration;
      source.start(at);
    },
    flush() {
      const unplayed = new Map<number, number>();
      const now = audio.currentTime;
      for (const [source, { at, samples, handle }] of sources) {
        const played = Math.max(0, Math.min(samples, Math.floor((now - at) * SAMPLE_RATE)));
        if (handle !== undefined && played < samples) {
          unplayed.set(handle, (unplayed.get(handle) ?? 0) + samples - played);
        }
        source.onended = null;
        source.stop();
        source.disconnect();
      }
      sources.clear();
      nextTime = 0;
      return [...unplayed];
    },
  };
}

interface VoiceOpening {
  workspace_id: string;
  profile_key: string;
  thread_id: string;
}

export function NativeVoiceControls({ eligible, opening, playingDictation, dictationActive = false, acquireVoiceInput, onThreadReady, onThreadRefresh, pageRoute, onNavigate, destinationRendered }: {
  eligible: boolean;
  pageRoute?: string;
  onNavigate?: (path: string, threadId: string) => boolean | void;
  destinationRendered?: (path: string) => boolean;
  dictationActive?: boolean;
  opening: VoiceOpening;
  playingDictation: () => boolean;
  acquireVoiceInput?: () => (() => void) | null;
  onThreadReady?: (threadId: string, opening: VoiceOpening) => Promise<void>;
  onThreadRefresh?: (threadId: string, workspace: string) => void;
}) {
  const [state, setState] = useState<NativeVoiceState>("idle");
  const [error, setError] = useState("");
  const [muted, setMuted] = useState(false);
  const startButton = useRef<HTMLButtonElement>(null);
  const endButton = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"idle" | "live" | null>(null);
  useEffect(() => {
    if (focusNext.current !== state) return;
    focusNext.current = null;
    (state === "live" ? endButton : startButton).current?.focus();
  }, [state]);
  const pageRouteRef = useRef(pageRoute);
  pageRouteRef.current = pageRoute;
  const navigateRef = useRef(onNavigate);
  navigateRef.current = onNavigate;
  const renderedRef = useRef(destinationRendered);
  renderedRef.current = destinationRendered;
  const call = useRef<{
    close: (refreshThread?: boolean) => void;
    matchesThread: (id: string) => boolean;
    toggleMicrophone: () => void;
    updatePage: () => void;
  } | null>(null);
  const { workspace_id, profile_key, thread_id } = opening;
  useEffect(() => {
    setState("idle");
    setError("");
    return () => call.current?.close();
  }, [workspace_id, profile_key, eligible]);

  useEffect(() => {
    if (call.current && !call.current.matchesThread(thread_id)) {
      call.current.close();
      setState("idle");
      setError("");
    }
  }, [thread_id]);

  useEffect(() => { call.current?.updatePage(); }, [pageRoute]);

  useEffect(() => {
    if (dictationActive && call.current) {
      call.current.close();
      setState("idle");
    }
  }, [dictationActive]);

  useEffect(() => {
    // A document refresh or BFCache navigation may not unmount React.
    function closeOnPageHide() {
      if (!call.current) return;
      call.current.close(false);
      setState("idle");
      setError("");
    }
    window.addEventListener("pagehide", closeOnPageHide);
    return () => window.removeEventListener("pagehide", closeOnPageHide);
  }, []);

  async function start() {
    if (call.current || !eligible) return;
    if (playingDictation()) {
      setError("Stop dictation before starting a voice call.");
      return;
    }
    const releaseVoiceInput = acquireVoiceInput?.();
    if (acquireVoiceInput && !releaseVoiceInput) {
      setError("Finish the other microphone session before starting voice.");
      return;
    }
    setError("");
    setMuted(false);
    setState("connecting");
    let media: MediaStream | undefined;
    let socket: WebSocket | undefined;
    let audio: AudioContext | undefined;
    let capture: AudioWorkletNode | undefined;
    let input: MediaStreamAudioSourceNode | undefined;
    let playback: ReturnType<typeof createVoicePlayback> | undefined;
    let ready = false;
    let microphoneMuted = false;
    let playbackHandle: number | undefined;
    let reviewSpeech: SpeechSynthesisUtterance | undefined;
    let reviewAudioSource: AudioBufferSourceNode | undefined;
    let reviewAudioActive = false;
    let interruptReview: (() => void) | undefined;
    let reviewInterruptionFrames = 0;
    let captureGeneration = 0;
    let acceptedThreadId = "";
    let pageRevision = 0;
    let refreshTimer: number | undefined;
    let ended = false;
    const close = (refreshThread = true) => {
      if (ended) return;
      ended = true;
      if (call.current?.close === close) call.current = null;
      window.clearTimeout(deadline);
      window.clearInterval(refreshTimer);
      const dispose = (operation: () => void) => {
        try { operation(); } catch { /* Continue releasing independent resources. */ }
      };
      try {
        if (capture) {
          capture.port.onmessage = null;
          dispose(() => capture?.disconnect());
        }
        dispose(() => input?.disconnect());
        media?.getTracks().forEach(track => {
          track.removeEventListener("ended", microphoneLost);
          dispose(() => track.stop());
        });
        dispose(() => playback?.flush());
        reviewAudioActive = false;
        if (reviewAudioSource) {
          reviewAudioSource.onended = null;
          dispose(() => reviewAudioSource?.stop());
          dispose(() => reviewAudioSource?.disconnect());
          reviewAudioSource = undefined;
        }
        if (reviewSpeech) {
          reviewSpeech.onend = reviewSpeech.onerror = null;
          dispose(() => window.speechSynthesis.cancel());
          reviewSpeech = undefined;
        }
        if (socket) {
          socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
          dispose(() => socket?.close());
        }
        dispose(() => { if (audio) void audio.close().catch(() => {}); });
      } finally {
        releaseVoiceInput?.();
      }
      // A view refresh is best-effort, never a prerequisite to hanging up.
      if (refreshThread && acceptedThreadId) {
        try { onThreadRefresh?.(acceptedThreadId, workspace_id); }
        catch { /* Media and transport have already been released. */ }
      }
    };
    const microphoneLost = () => fail("The microphone was disconnected. Reconnect it and start a new call.");
    const fail = (message: string) => {
      if (ended) return;
      if (document.activeElement === endButton.current) focusNext.current = "idle";
      const wasLive = ready;
      close();
      if (wasLive) announceLocalVoiceFailure(
        message === "The microphone was disconnected. Reconnect it and start a new call."
          ? message
          : "The voice call ended. Start a new call to continue.",
      );
      setState("idle");
      setError(message);
    };
    const deadline = window.setTimeout(() => fail("The voice call did not connect."), 20000);
    const updatePage = () => {
      const route = pageRouteRef.current;
      if (!route || !ready || socket?.readyState !== WebSocket.OPEN) return;
      pageRevision += 1;
      socket.send(JSON.stringify({ type: "page_context", page: {
        url: route, route_path: route.split("?")[0],
      }}));
    };
    call.current = {
      close,
      updatePage,
      matchesThread: id => id === (acceptedThreadId || thread_id),
      toggleMicrophone: () => {
        if (ended || !ready || !capture || !media) return;
        microphoneMuted = !microphoneMuted;
        captureGeneration += 1;
        // Gate frames synchronously, even if already queued on the worklet port.
        // The worklet also discards its partially filled block on every toggle.
        const tracks = media.getAudioTracks();
        if (microphoneMuted) for (const track of tracks) track.enabled = false;
        try {
          if (socket?.readyState !== WebSocket.OPEN) throw new Error("Voice transport is not open");
          socket.send(JSON.stringify({ type: "microphone", muted: microphoneMuted }));
          capture.port.postMessage({ muted: microphoneMuted, generation: captureGeneration });
        } catch {
          fail("The voice call ended.");
          return;
        }
        if (!microphoneMuted) for (const track of tracks) track.enabled = true;
        setMuted(microphoneMuted);
      },
    };
    try {
      audio = new AudioContext({ sampleRate: SAMPLE_RATE });
      await audio.resume();
      if (ended) return;
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      if (ended) { stream.getTracks().forEach(track => track.stop()); return; }
      media = stream;
      for (const track of stream.getAudioTracks()) track.addEventListener("ended", microphoneLost);
      await audio.audioWorklet.addModule(captureModuleUrl);
      if (ended) return;
      playback = createVoicePlayback(audio);
      input = audio.createMediaStreamSource(stream);
      capture = new AudioWorkletNode(audio, "integral-native-voice", { numberOfOutputs: 0 });
      capture.port.onmessage = ({ data }: MessageEvent<{ generation: number; pcm: ArrayBuffer }>) => {
        if (ended || microphoneMuted || data.generation !== captureGeneration) return;
        if (reviewSpeech || reviewAudioActive) {
          // Do not send microphone audio to either provider while the local
          // readback speaks. Sustained input interrupts the challenge.
          const samples = new Int16Array(data.pcm);
          const energy = samples.reduce((sum, sample) => sum + sample * sample, 0) / Math.max(1, samples.length);
          reviewInterruptionFrames = energy > 1800 ** 2 ? reviewInterruptionFrames + 1 : 0;
          if (reviewInterruptionFrames >= 8) interruptReview?.();
          return;
        }
        if (ready && socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < 96_000) socket.send(data.pcm);
      };
      input.connect(capture);
      const ticket = await mintWsTicket();
      if (ended) return;
      if (!ticket) throw new Error("Authentication required");
      const endpoint = new URL("/ws/native-voice", window.location.href);
      endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(endpoint);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => socket?.send(JSON.stringify({ ticket, ...opening, review_confirmation: true }));
      socket.onmessage = ({ data }) => {
        if (ended) return;
        if (data instanceof ArrayBuffer) {
          if (ready) playback?.enqueue(data, playbackHandle);
          return;
        }
        const event = JSON.parse(String(data)) as { type: string; message?: string; thread_id?: string; handle?: number | null; id?: string; prompt?: string; audio?: string; origin_revision?: number; navigation?: unknown };
        if (event.type === "navigation" && ready && acceptedThreadId && event.id) {
          const path = agentNavigationPath({ ok: true, navigation: event.navigation }, acceptedThreadId, workspace_id);
          const report = (status: string) => {
            if (!ended && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "navigation_result", id: event.id, status }));
          };
          const composing = [...document.querySelectorAll<HTMLTextAreaElement>('textarea[aria-label="Message input"]')].some(input => Boolean(input.value.trim()));
          if (!path || event.origin_revision !== pageRevision || document.visibilityState !== "visible" || composing || document.querySelector('button[aria-label="Remove attachment"]') || !navigateRef.current) {
            report("preserved");
            return;
          }
          if (navigateRef.current(path, acceptedThreadId) === false) {
            report("preserved");
            return;
          }
          const deadline = performance.now() + 10000;
          const checkRendered = () => {
            if (ended) return;
            if (document.visibilityState === "visible" && pageRouteRef.current === path && renderedRef.current?.(path)) report("rendered");
            else if (performance.now() >= deadline) report("unavailable");
            else window.setTimeout(checkRendered, 100);
          };
          checkRendered();
          return;
        }
        if (event.type === "review_confirmation" && ready && event.id && event.prompt) {
          const id = event.id;
          const notify = (type: "review_prompt_finished" | "review_prompt_unavailable" | "review_prompt_interrupted") => {
            if (!ended && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type, id }));
          };
          playback?.flush();
          try {
            const synth = window.speechSynthesis;
            const voice = synth?.getVoices().find(candidate => candidate.localService && candidate.lang.toLowerCase().startsWith("en"));
            if (!voice || typeof SpeechSynthesisUtterance === "undefined" || reviewSpeech) {
              notify("review_prompt_unavailable");
              return;
            }
            const utterance = new SpeechSynthesisUtterance(event.prompt);
            utterance.voice = voice;
            reviewSpeech = utterance;
            reviewInterruptionFrames = 0;
            interruptReview = () => {
              if (reviewSpeech !== utterance) return;
              utterance.onend = utterance.onerror = null;
              reviewSpeech = undefined;
              interruptReview = undefined;
              synth.cancel();
              notify("review_prompt_interrupted");
            };
            utterance.onend = () => {
              if (reviewSpeech !== utterance) return;
              reviewSpeech = undefined;
              interruptReview = undefined;
              captureGeneration += 1;
              capture?.port.postMessage({ muted: microphoneMuted, generation: captureGeneration });
              notify("review_prompt_finished");
            };
            utterance.onerror = () => {
              if (reviewSpeech !== utterance) return;
              reviewSpeech = undefined;
              interruptReview = undefined;
              notify("review_prompt_unavailable");
            };
            synth.speak(utterance);
          } catch {
            reviewSpeech = undefined;
            notify("review_prompt_unavailable");
          }
          return;
        }
        if (event.type === "review_confirmation_audio" && ready && event.id && event.audio) {
          const id = event.id;
          const notify = (type: "review_prompt_finished" | "review_prompt_unavailable" | "review_prompt_interrupted") => {
            if (!ended && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type, id }));
          };
          if (!audio || reviewSpeech || reviewAudioActive || event.audio.length > 8_000_000) {
            notify("review_prompt_unavailable");
            return;
          }
          reviewAudioActive = true;
          reviewInterruptionFrames = 0;
          const cancelAudio = () => {
            reviewAudioActive = false;
            interruptReview = undefined;
            if (reviewAudioSource) {
              reviewAudioSource.onended = null;
              try { reviewAudioSource.stop(); } catch { /* Already stopped. */ }
              reviewAudioSource.disconnect();
              reviewAudioSource = undefined;
            }
          };
          interruptReview = () => { cancelAudio(); notify("review_prompt_interrupted"); };
          const context = audio;
          void (async () => {
            try {
              const bytes = Uint8Array.from(atob(event.audio!), character => character.charCodeAt(0));
              const buffer = await context.decodeAudioData(bytes.buffer);
              if (ended || !reviewAudioActive) return;
              const source = context.createBufferSource();
              source.buffer = buffer;
              source.connect(context.destination);
              reviewAudioSource = source;
              source.onended = () => {
                if (reviewAudioSource !== source) return;
                source.disconnect();
                reviewAudioSource = undefined;
                reviewAudioActive = false;
                interruptReview = undefined;
                captureGeneration += 1;
                capture?.port.postMessage({ muted: microphoneMuted, generation: captureGeneration });
                notify("review_prompt_finished");
              };
              source.start();
            } catch {
              if (reviewAudioActive) { cancelAudio(); notify("review_prompt_unavailable"); }
            }
          })();
          return;
        }
        if (event.type === "review_confirmation_ended") {
          interruptReview = undefined;
          reviewAudioActive = false;
          if (reviewAudioSource) {
            reviewAudioSource.onended = null;
            try { reviewAudioSource.stop(); } catch { /* Already stopped. */ }
            reviewAudioSource.disconnect();
            reviewAudioSource = undefined;
          }
          if (reviewSpeech) {
            reviewSpeech.onend = reviewSpeech.onerror = null;
            reviewSpeech = undefined;
            window.speechSynthesis.cancel();
          }
        }
        if (event.type === "playback_item") playbackHandle = event.handle ?? undefined;
        if (event.type === "flush") {
          const items = playback?.flush();
          playbackHandle = undefined;
          if (items?.length && socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "playback_interrupted", items }));
          }
        }
        // Transport open is not provider ready. Request one opening only
        // after the relay acknowledges the session. The relay gives early
        // user speech precedence and owns the generated turn.
        if (event.type === "ready" && !ready) {
          if (event.thread_id && thread_id && event.thread_id !== thread_id) {
            fail("The voice call returned a different conversation.");
            return;
          }
          acceptedThreadId = event.thread_id || thread_id;
          if (acceptedThreadId) {
            void onThreadReady?.(acceptedThreadId, opening);
            if (onThreadRefresh) {
              refreshTimer = window.setInterval(() => {
                if (document.visibilityState === "visible") onThreadRefresh(acceptedThreadId, workspace_id);
              }, 2500);
            }
          }
          ready = true;
          updatePage();
          window.clearTimeout(deadline);
          if (document.activeElement === startButton.current) focusNext.current = "live";
          setState("live");
          if (socket?.readyState === WebSocket.OPEN) {
            try { socket.send(JSON.stringify({ type: "opening" })); }
            catch { fail("The voice call ended."); }
          }
        }
        if (event.type === "error") fail(event.message || "The voice call ended.");
      };
      socket.onerror = () => fail("The voice call could not connect.");
      socket.onclose = () => fail("The voice call ended.");
    } catch {
      fail("Could not start live voice. Check your microphone permission and model settings.");
    }
  }

  if (!eligible && state === "idle") return null;
  const idle = state === "idle";
  const live = state === "live";
  const action = idle ? "Start voice call" : live ? "End call" : "Cancel voice call";
  const microphoneAction = muted ? "Unmute microphone" : "Mute microphone";
  const liftAboveTooltip = "group-hover/voice:-translate-y-8 group-focus-within/voice:-translate-y-8";
  return <div className="group/voice relative inline-flex shrink-0 items-center gap-1 hover:z-20 focus-within:z-20">
    {live ? (
      <Surface role="group" aria-label="Live voice call controls" tone="panel-2" radius="pill"
        border="default" padding="none" className="inline-flex max-w-full shrink-0 items-center gap-0.5 p-0.5">
        <div role="status" className="inline-flex items-center gap-1 pl-2 pr-0.5">
          <AudioLines size={14} aria-hidden />
          <span className="sr-only">{muted ? "Voice call live · Mic muted" : "Voice call live"}</span>
          <Text as="span" variant="body-sm" aria-hidden="true" className="hidden sm:inline">Live</Text>
        </div>
        <HoverTooltip label={microphoneAction} placement="top">
          <Button type="button" variant={muted ? "secondary" : "ghost"} size="xs"
            className="h-9 w-9 rounded-full !p-0" aria-label={microphoneAction}
            icon={muted
              ? <MicOff size={17} aria-hidden className="text-[var(--danger-fg)]" />
              : <Mic size={17} aria-hidden />}
            onClick={() => call.current?.toggleMicrophone()} />
        </HoverTooltip>
        <Button ref={endButton} type="button" variant="danger" size="xs" aria-label="End call"
          className="h-9 whitespace-nowrap rounded-full px-2"
          icon={<Square size={12} aria-hidden />}
          onClick={() => {
            if (document.activeElement === endButton.current) focusNext.current = "idle";
            call.current?.close();
            setState("idle");
          }}>
          <span className="hidden sm:inline">End call</span>
        </Button>
      </Surface>
    ) : (
      <>
        {!idle && <Surface role="status" radius="input" elevation="sm" padding="xs"
          className={`absolute bottom-full right-0 z-10 mb-2 inline-flex w-max items-center gap-2 ${liftAboveTooltip}`}>
          <Loader2 size={14} aria-hidden className="motion-safe:animate-spin" />
          <Text as="span" variant="body-sm">Connecting voice…</Text>
        </Surface>}
        <HoverTooltip label={action} placement="top">
          <Button ref={startButton} type="button" variant={idle ? "ghost" : "danger"} size="xs"
            className="h-10 w-10 rounded-full !p-0"
            aria-label={action}
            icon={idle ? <AudioLines size={17} aria-hidden /> : <Square size={12} aria-hidden />}
            onClick={() => {
              if (idle) void start();
              else { call.current?.close(); setState("idle"); }
            }} />
        </HoverTooltip>
      </>
    )}
    {error && <Surface role="status" radius="input" elevation="sm" padding="sm"
      className={`absolute bottom-full right-0 z-10 mb-2 w-56 ${liftAboveTooltip}`}><Text as="p" variant="body-sm">{error}</Text></Surface>}
  </div>;
}

export function NativeVoiceCall() {
  const activity = useChatActivity();
  const location = useLocation();
  const navigate = useNavigate();
  const snapshot = useChatPageContextSnapshot();
  const dictation = useComposerDictationActions();
  const dictationStatus = useComposerDictation()?.status;
  const workspace = useScopeOptional()?.scope?.workspaceId ?? "";
  const user = useAuthOptional()?.user?.id;
  const thread = activity.threads.find(item => item.id === activity.activeThreadId);
  const target = thread ? thread.agent_profile_key ?? "" : activity.profileFilter;
  const profile = !target || target === WORKSPACE_ASSISTANT.key ? "" : target;
  const native = activity.activeProviderId === "integral_native" && Boolean(user && workspace);
  const configuration = useQuery({
    queryKey: [...REALTIME_VOICE_QUERY_KEY, user, workspace],
    queryFn: () => realtimeVoiceConfigurationApi.get(),
    enabled: native,
  });
  const eligible = native && Boolean(configuration.data?.effective_model) &&
    Boolean(navigator.mediaDevices?.getUserMedia) && profile !== ALL_WORKSPACE_ASSISTANTS &&
    (!activity.activeThreadId || Boolean(thread)) && !activity.needsDraftAgent;
  return <NativeVoiceControls eligible={eligible}
    pageRoute={location.pathname + location.search}
    destinationRendered={path => {
      const page = snapshot();
      const destination = new URL(path, window.location.origin);
      const id = destination.pathname.split("/")[2];
      if (destination.pathname.startsWith("/apps/")) return page.focused_app_id === id && Boolean(page.metadata?.app_name);
      if (destination.searchParams.has("entry")) return page.focused_entry_id === destination.searchParams.get("entry") && Boolean(page.metadata?.entry_title);
      if (destination.pathname.startsWith("/entries/")) return page.focused_entry_id === id && Boolean(page.metadata?.entry_title);
      return page.focused_track_id === id && Boolean(page.metadata?.track_title) && (!destination.searchParams.has("view_id") || page.focused_view_id === destination.searchParams.get("view_id"));
    }}
    onNavigate={(path, threadId) => {
      if (activity.followUps?.some(item => item.state === "queued" || item.state === "sending")) return false;
      requestOpenCompanionChat({ threadId, workspaceId: workspace, principalId: user });
      navigate(path);
    }}
    opening={{ workspace_id: workspace, profile_key: profile, thread_id: activity.activeThreadId ?? "" }}
    onThreadReady={activity.adoptNativeVoiceThread}
    onThreadRefresh={activity.refreshNativeVoiceThread}
    playingDictation={() => dictation?.isListening() ?? false}
    acquireVoiceInput={dictation?.acquireVoiceInput}
    dictationActive={Boolean(dictationStatus && ["requesting-permission", "connecting", "listening", "finalizing"].includes(dictationStatus))} />;
}
