import { useEffect, useRef, useState } from "react";
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

const SAMPLE_RATE = 24000;
const CAPTURE = new URL("./nativeVoiceCapture.js", import.meta.url);
export type NativeVoiceState = "idle" | "connecting" | "live";

export function createVoicePlayback(audio: AudioContext) {
  const sources = new Set<AudioBufferSourceNode>();
  let nextTime = 0;
  return {
    enqueue(data: ArrayBuffer) {
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
      sources.add(source);
      const at = Math.max(audio.currentTime + 0.02, nextTime);
      nextTime = at + buffer.duration;
      source.start(at);
    },
    flush() {
      for (const source of sources) {
        source.onended = null;
        source.stop();
        source.disconnect();
      }
      sources.clear();
      nextTime = 0;
    },
  };
}

interface VoiceOpening {
  workspace_id: string;
  profile_key: string;
  thread_id: string;
}

export function NativeVoiceControls({ eligible, opening, playingDictation, dictationActive = false, acquireVoiceInput, onThreadReady, onThreadRefresh }: {
  eligible: boolean;
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
  const call = useRef<{
    close: (refreshThread?: boolean) => void;
    matchesThread: (id: string) => boolean;
    toggleMicrophone: () => void;
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
    let captureGeneration = 0;
    let acceptedThreadId = "";
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
        media?.getTracks().forEach(track => dispose(() => track.stop()));
        dispose(() => playback?.flush());
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
    const fail = (message: string) => {
      if (ended) return;
      close();
      setState("idle");
      setError(message);
    };
    const deadline = window.setTimeout(() => fail("The voice call did not connect."), 20000);
    call.current = {
      close,
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
      await audio.audioWorklet.addModule(CAPTURE);
      if (ended) return;
      playback = createVoicePlayback(audio);
      input = audio.createMediaStreamSource(stream);
      capture = new AudioWorkletNode(audio, "integral-native-voice", { numberOfOutputs: 0 });
      capture.port.onmessage = ({ data }: MessageEvent<{ generation: number; pcm: ArrayBuffer }>) => {
        if (ended || microphoneMuted || data.generation !== captureGeneration) return;
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
      socket.onopen = () => socket?.send(JSON.stringify({ ticket, ...opening }));
      socket.onmessage = ({ data }) => {
        if (ended) return;
        if (data instanceof ArrayBuffer) {
          if (ready) playback?.enqueue(data);
          return;
        }
        const event = JSON.parse(String(data)) as { type: string; message?: string; thread_id?: string };
        if (event.type === "flush") playback?.flush();
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
    opening={{ workspace_id: workspace, profile_key: profile, thread_id: activity.activeThreadId ?? "" }}
    onThreadReady={activity.adoptNativeVoiceThread}
    onThreadRefresh={activity.refreshNativeVoiceThread}
    playingDictation={() => dictation?.isListening() ?? false}
    acquireVoiceInput={dictation?.acquireVoiceInput}
    dictationActive={Boolean(dictationStatus && ["requesting-permission", "connecting", "listening", "finalizing"].includes(dictationStatus))} />;
}
