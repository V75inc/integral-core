import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Phone, PhoneOff } from "lucide-react";
import { REALTIME_VOICE_QUERY_KEY, realtimeVoiceConfigurationApi } from "../../../api/aiConfiguration";
import { ALL_WORKSPACE_ASSISTANTS, WORKSPACE_ASSISTANT } from "../../../api/chatAssistantIdentity";
import { mintWsTicket } from "../../../api/wsTicket";
import { Button } from "../../../components/ui/Button";
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

export function NativeVoiceControls({ eligible, opening, playingDictation, dictationActive = false }: {
  eligible: boolean;
  dictationActive?: boolean;
  opening: VoiceOpening;
  playingDictation: () => boolean;
}) {
  const [state, setState] = useState<NativeVoiceState>("idle");
  const [error, setError] = useState("");
  const call = useRef<{ close: () => void } | null>(null);
  const { workspace_id, profile_key, thread_id } = opening;
  useEffect(() => {
    setState("idle");
    setError("");
    return () => call.current?.close();
  }, [workspace_id, profile_key, thread_id, eligible]);

  useEffect(() => {
    if (dictationActive && call.current) {
      call.current.close();
      setState("idle");
    }
  }, [dictationActive]);

  async function start() {
    if (call.current || !eligible) return;
    if (playingDictation()) {
      setError("Stop dictation before starting a voice call.");
      return;
    }
    setError("");
    setState("connecting");
    let media: MediaStream | undefined;
    let socket: WebSocket | undefined;
    let audio: AudioContext | undefined;
    let capture: AudioWorkletNode | undefined;
    let input: MediaStreamAudioSourceNode | undefined;
    let playback: ReturnType<typeof createVoicePlayback> | undefined;
    let ready = false;
    let ended = false;
    const close = () => {
      if (ended) return;
      ended = true;
      if (call.current?.close === close) call.current = null;
      window.clearTimeout(deadline);
      if (capture) { capture.port.onmessage = null; capture.disconnect(); }
      input?.disconnect();
      media?.getTracks().forEach(track => track.stop());
      playback?.flush();
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        socket.close();
      }
      if (audio) void audio.close().catch(() => {});
    };
    const fail = (message: string) => {
      if (ended) return;
      close();
      setState("idle");
      setError(message);
    };
    const deadline = window.setTimeout(() => fail("The voice call did not connect."), 20000);
    call.current = { close };
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
      capture.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
        if (ready && socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < 96_000) socket.send(data);
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
        if (data instanceof ArrayBuffer) { playback?.enqueue(data); return; }
        const event = JSON.parse(String(data)) as { type: string; message?: string };
        if (event.type === "flush") playback?.flush();
        if (event.type === "ready") {
          ready = true;
          window.clearTimeout(deadline);
          setState("live");
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
  const label = state === "idle" ? "Start voice call" : "End voice call";
  return <div className="relative shrink-0">
    <Button type="button" variant={state === "idle" ? "ghost" : "danger"} size="sm"
      className="h-10 w-10 rounded-full !p-0" aria-label={label} title={label}
      icon={state === "idle" ? <Phone size={16} aria-hidden /> : <PhoneOff size={16} aria-hidden />}
      onClick={() => {
        if (state === "idle") void start();
        else { call.current?.close(); setState("idle"); }
      }} />
    {state === "connecting" && <span className="sr-only" role="status">Connecting voice call</span>}
    {state === "live" && <span className="sr-only" role="status">Voice call connected</span>}
    {error && <Surface role="status" radius="input" elevation="sm" padding="sm"
      className="absolute bottom-full right-0 z-10 mb-2 w-56"><Text as="p" variant="body-sm">{error}</Text></Surface>}
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
    playingDictation={() => dictation?.isListening() ?? false}
    dictationActive={Boolean(dictationStatus && ["connecting", "listening", "finalizing"].includes(dictationStatus))} />;
}
