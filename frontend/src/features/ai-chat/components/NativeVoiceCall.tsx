import { claimVoiceSession, peekVoiceSession, releaseVoiceSession, updateVoiceSessionThread, useVoiceSession } from "../nativeVoiceSession";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { clearCallReview, presentCallReview } from "../voiceReviewPresentation";
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
const CONNECT_TIMEOUT_MS = 20_000;

/** Quiet, local-only ringback. Two soft pulses with silence between pairs. */
export function createVoiceRingback(audio: AudioContext): (() => void) | undefined {
  if (audio.state !== "running") return;
  let gain: GainNode | undefined;
  const tones: OscillatorNode[] = [];
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (gain) {
      try { gain.gain.cancelScheduledValues(audio.currentTime); gain.gain.setValueAtTime(0, audio.currentTime); } catch { /* Context already closed. */ }
    }
    for (const tone of tones) {
      try { tone.stop(); } catch { /* Already stopped. */ }
      try { tone.disconnect(); } catch { /* Graph already released. */ }
    }
    try { gain?.disconnect(); } catch { /* Graph already released. */ }
  };
  try {
    gain = audio.createGain();
    const start = audio.currentTime + 0.015;
    const end = start + CONNECT_TIMEOUT_MS / 1000;
    gain.gain.setValueAtTime(0, audio.currentTime);
    gain.connect(audio.destination); // Never connected to the capture graph.
    for (let pair = start; pair + 0.55 < end; pair += 2.4) {
      for (const at of [pair, pair + 0.29]) {
        gain.gain.setValueAtTime(0, at);
        gain.gain.linearRampToValueAtTime(0.018, at + 0.025);
        gain.gain.setValueAtTime(0.018, at + 0.135);
        gain.gain.linearRampToValueAtTime(0, at + 0.19);
      }
    }
    for (const frequency of [440, 480]) {
      const tone = audio.createOscillator();
      tones.push(tone);
      tone.type = "sine";
      tone.frequency.value = frequency;
      tone.connect(gain);
      tone.start(start);
      tone.stop(end);
    }
    return stop;
  } catch {
    stop(); // Ringback is optional; an audio-policy failure is not a call failure.
    return undefined;
  }
}

export type NativeVoiceState = "idle" | "connecting" | "live";

export function createVoicePlayback(audio: AudioContext) {
  const sources = new Map<AudioBufferSourceNode, { at: number; samples: number; handle?: number }>();
  let nextTime = 0;
  function enqueueBuffer(buffer: AudioBuffer, handle?: number) {
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    source.onended = () => { sources.delete(source); source.disconnect(); };
    const at = Math.max(audio.currentTime + 0.02, nextTime);
    sources.set(source, { at, samples: Math.round(buffer.duration * SAMPLE_RATE), handle });
    nextTime = at + buffer.duration;
    source.start(at);
  }
  return {
    enqueue(data: ArrayBuffer, handle?: number) {
      if (data.byteLength === 0 || data.byteLength % 2 !== 0) return;
      const pcm = new DataView(data);
      const buffer = audio.createBuffer(1, data.byteLength / 2, SAMPLE_RATE);
      const samples = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index += 1) {
        samples[index] = pcm.getInt16(index * 2, true) / 32768;
      }
      enqueueBuffer(buffer, handle);
    },
    enqueueBuffer,
    remainingMs: () => Math.max(0, (nextTime - audio.currentTime) * 1000),
    flush(ordinaryOnly = false) {
      const unplayed = new Map<number, number>();
      const now = audio.currentTime;
      for (const [source, { at, samples, handle }] of sources) {
        if (ordinaryOnly && handle === undefined) continue;
        const played = Math.max(0, Math.min(samples, Math.floor((now - at) * SAMPLE_RATE)));
        if (handle !== undefined && played < samples) {
          unplayed.set(handle, (unplayed.get(handle) ?? 0) + samples - played);
        }
        source.onended = null;
        source.stop();
        source.disconnect();
        sources.delete(source);
      }
      // A prior committed result is not low-priority speech. Keep its audio
      // and schedule the next result after it, rather than overlapping it.
      nextTime = Math.max(0, ...Array.from(sources.values(), item => item.at + item.samples / SAMPLE_RATE));
      return [...unplayed];
    },
  };
}

interface VoiceOpening {
  workspace_id: string;
  profile_key: string;
  thread_id: string;
}

export function NativeVoiceControls({ surface = "dock", eligible, opening, playingDictation, dictationActive = false, acquireVoiceInput, onThreadReady, onThreadRefresh, pageRoute, onNavigate, destinationRendered }: {
  surface?: "dock" | "home";
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
  const owner = useRef(Symbol("voice call"));
  const activeVoice = useVoiceSession();
  const [state, setState] = useState<NativeVoiceState>("idle");
  const [error, setError] = useState("");
  const [muted, setMuted] = useState(false);
  const [audioInterrupted, setAudioInterrupted] = useState(false);
  const [endingCall, setEndingCall] = useState(false);
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

  async function start() {
    if (call.current || peekVoiceSession() || !eligible) return;
    if (playingDictation()) {
      setError("Stop dictation before starting a voice call.");
      return;
    }
    const releaseVoiceInput = acquireVoiceInput?.();
    if (acquireVoiceInput && !releaseVoiceInput) {
      setError("Finish the other microphone session before starting voice.");
      return;
    }
    const callOwner = Symbol("voice call");
    owner.current = callOwner;
    setError("");
    setMuted(false);
    setAudioInterrupted(false);
    setEndingCall(false);
    setState("connecting");
    let media: MediaStream | undefined;
    let socket: WebSocket | undefined;
    let audio: AudioContext | undefined;
    let capture: AudioWorkletNode | undefined;
    let stopRing: (() => void) | undefined;
    let captureEnableTimer: number | undefined;
    let captureEnabled = false;
    const stopRingback = () => { stopRing?.(); stopRing = undefined; };
    let input: MediaStreamAudioSourceNode | undefined;
    let playback: ReturnType<typeof createVoicePlayback> | undefined;
    let ready = false;
    let microphoneMuted = false;
    let deviceMuted = false;
    let captureInterrupted = false;
    let lastCapture = performance.now();
    let captureWatch: number | undefined;
    let playbackHandle: number | undefined;
    let reviewId: string | undefined;
    let reviewAudioSource: AudioBufferSourceNode | undefined;
    let reviewAudioActive = false;
    let interruptReview: (() => void) | undefined;
    let reviewInterruptionFrames = 0;
    let captureGeneration = 0;
    const reviewOwner = Symbol();
    let acceptedThreadId = "";
    let pageRevision = 0;
    let refreshTimer: number | undefined;
    let ended = false;
    let endingRequested = false;
    let endTimer: number | undefined;
    const close = (refreshThread = true) => {
      if (ended) return;
      ended = true;
      clearCallReview(reviewOwner);
      if (call.current?.close === close) call.current = null;
      releaseVoiceSession(callOwner);
      window.clearTimeout(deadline);
      window.clearTimeout(endTimer);
      window.clearTimeout(captureEnableTimer);
      stopRingback();
      setEndingCall(false);
      window.clearInterval(refreshTimer);
      window.clearInterval(captureWatch);
      audio?.removeEventListener?.("statechange", updateCaptureState);
      setAudioInterrupted(false);
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
          track.removeEventListener("mute", deviceInterrupted);
          track.removeEventListener("unmute", deviceRestored);
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
    // OS audio-route interruption is distinct from the user's mute choice.
    // Keep the call and its resident task alive; fence partial capture through
    // the same relay control used for deliberate mute. Resume only when actual
    // capture returns and the user has not deliberately muted the microphone.
    const syncCapture = () => {
      if (ended || endingRequested || !ready || !capture) return;
      const blocked = deviceMuted || captureInterrupted;
      setAudioInterrupted(blocked);
      if (socket?.readyState !== WebSocket.OPEN) return;
      captureGeneration += 1;
      try {
        socket.send(JSON.stringify({ type: "microphone", muted: blocked || microphoneMuted }));
        capture.port.postMessage({ muted: deviceMuted || microphoneMuted, generation: captureGeneration });
      } catch { fail("The voice call ended."); }
    };
    const updateCaptureState = () => {
      const interrupted = !!audio && audio.state !== undefined && audio.state !== "running";
      if (interrupted) stopRingback();
      if (captureInterrupted === interrupted) return;
      captureInterrupted = interrupted;
      lastCapture = performance.now();
      syncCapture();
    };
    const deviceInterrupted = () => { deviceMuted = true; stopRingback(); syncCapture(); };
    const deviceRestored = () => { deviceMuted = false; lastCapture = performance.now(); syncCapture(); };
    const microphoneLost = () => endingRequested ? finishRequestedEnd() : fail("The microphone was disconnected. Reconnect it and start a new call.");
    const fail = (message: string) => {
      if (ended) return;
      if (document.activeElement === endButton.current) focusNext.current = "idle";
      close();
      setState("idle");
      setError(message);
    };
    const finishRequestedEnd = () => {
      if (ended) return;
      if (document.activeElement === endButton.current) focusNext.current = "idle";
      close();
      setState("idle");
      setError("");
    };
    const deadline = window.setTimeout(() => fail("The voice call did not connect."), CONNECT_TIMEOUT_MS);
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
        if (ended || endingRequested || !ready || !capture || !media) return;
        microphoneMuted = !microphoneMuted;
        captureGeneration += 1;
        // Gate frames synchronously, even if already queued on the worklet port.
        // The worklet also discards its partially filled block on every toggle.
        const tracks = media.getAudioTracks();
        if (microphoneMuted) for (const track of tracks) track.enabled = false;
        try {
          if (socket?.readyState !== WebSocket.OPEN) throw new Error("Voice transport is not open");
          socket.send(JSON.stringify({ type: "microphone", muted: microphoneMuted || deviceMuted || captureInterrupted }));
          capture.port.postMessage({ muted: microphoneMuted || deviceMuted, generation: captureGeneration });
        } catch {
          fail("The voice call ended.");
          return;
        }
        if (!microphoneMuted && captureEnabled) for (const track of tracks) track.enabled = true;
        lastCapture = performance.now();
        setMuted(microphoneMuted);
      },
    };
    if (!claimVoiceSession({
      owner: callOwner, surface, workspaceId: workspace_id, threadId: thread_id,
      close: refresh => { close(refresh); setState("idle"); setError(""); },
    })) { close(); setState("idle"); return; }
    try {
      audio = new AudioContext({ sampleRate: SAMPLE_RATE });
      await audio.resume();
      if (ended) return;
      // The user gesture that started this call owns this AudioContext.
      audio.addEventListener?.("statechange", updateCaptureState);
      stopRing = createVoiceRingback(audio);
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      if (ended) { stream.getTracks().forEach(track => track.stop()); return; }
      media = stream;
      for (const track of stream.getAudioTracks()) {
        track.enabled = false; // No capture of locally generated ringback before readiness.
        track.addEventListener("ended", microphoneLost);
        track.addEventListener("mute", deviceInterrupted);
        track.addEventListener("unmute", deviceRestored);
        deviceMuted ||= track.muted === true;
      }
      if (deviceMuted) stopRingback();
      await audio.audioWorklet.addModule(captureModuleUrl);
      if (ended) return;
      playback = createVoicePlayback(audio);
      input = audio.createMediaStreamSource(stream);
      capture = new AudioWorkletNode(audio, "integral-native-voice", { numberOfOutputs: 0, processorOptions: { muted: true } });
      capture.port.onmessage = ({ data }: MessageEvent<{ generation: number; pcm: ArrayBuffer }>) => {
        if (ended || !captureEnabled || endingRequested || microphoneMuted || deviceMuted || data.generation !== captureGeneration) return;
        lastCapture = performance.now();
        if (captureInterrupted) {
          if (audio?.state && audio.state !== "running") return;
          captureInterrupted = false;
          syncCapture();
          return;
        }
        if (reviewId || reviewAudioActive) {
          // Do not send microphone audio to either provider while the verified
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
      socket.onopen = () => socket?.send(JSON.stringify({ ticket, ...opening, review_confirmation: true, conversational_review: true }));
      socket.onmessage = ({ data }) => {
        if (ended) return;
        if (data instanceof ArrayBuffer) {
          if (ready) playback?.enqueue(data, playbackHandle);
          return;
        }
        const event = JSON.parse(String(data)) as { type: string; message?: string; thread_id?: string; handle?: number | null; report_handles?: number[]; id?: string; proposal_id?: string; audio?: string; origin_revision?: number; navigation?: unknown };
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
        if (event.type === "review_details_required" && ready && acceptedThreadId && event.proposal_id) {
          presentCallReview(reviewOwner, acceptedThreadId, event.proposal_id, true);
          return;
        }
        if (event.type === "review_confirmation" && ready && event.id) {
          if (acceptedThreadId && event.proposal_id) presentCallReview(reviewOwner, acceptedThreadId, event.proposal_id);
          reviewId = event.id;
          playback?.flush();
          return;
        }
        if (event.type === "review_confirmation_audio" && ready && event.id && event.audio) {
          const id = event.id;
          if (id !== reviewId) return;
          const notify = (type: "review_prompt_finished" | "review_prompt_unavailable" | "review_prompt_interrupted") => {
            if (!ended && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type, id }));
          };
          if (!audio || reviewAudioActive || event.audio.length > 8_000_000) {
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
              if (ended || !reviewAudioActive || reviewId !== id) return;
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
                reviewId = undefined;
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
          if (acceptedThreadId && !opening.profile_key) presentCallReview(reviewOwner, acceptedThreadId);
          interruptReview = undefined;
          reviewAudioActive = false;
          if (reviewAudioSource) {
            reviewAudioSource.onended = null;
            try { reviewAudioSource.stop(); } catch { /* Already stopped. */ }
            reviewAudioSource.disconnect();
            reviewAudioSource = undefined;
          }
          reviewId = undefined;
        }
        if (event.type === "playback_item") playbackHandle = event.handle ?? undefined;
        if (event.type === "flush" || event.type === "flush_ordinary") {
          const items = playback?.flush(event.type === "flush_ordinary") ?? [];
          if (event.type === "flush_ordinary") {
            for (const handle of event.report_handles ?? []) {
              if (!items.some(([reported]) => reported === handle)) items.push([handle, 0]);
            }
          }
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
          updateVoiceSessionThread(callOwner, acceptedThreadId);
          if (acceptedThreadId) {
            if (!opening.profile_key) presentCallReview(reviewOwner, acceptedThreadId);
            void onThreadReady?.(acceptedThreadId, opening);
            if (onThreadRefresh) {
              refreshTimer = window.setInterval(() => {
                if (document.visibilityState === "visible") onThreadRefresh(acceptedThreadId, workspace_id);
              }, 2500);
            }
          }
          const wasRinging = Boolean(stopRing);
          stopRingback();
          ready = true;
          lastCapture = performance.now();
          const enableCapture = () => {
            if (ended || endingRequested || !capture || !media) return;
            captureEnabled = true;
            lastCapture = performance.now();
            if (deviceMuted || captureInterrupted) syncCapture();
            else capture.port.postMessage({ muted: microphoneMuted, generation: captureGeneration });
            if (!microphoneMuted) for (const track of media.getAudioTracks()) track.enabled = true;
          };
          // The local oscillator is already stopped. Allow the device's last
          // small output buffer to drain before enabling microphone capture.
          const outputTail = wasRinging && audio ? Math.min(150, Math.ceil(((audio.baseLatency || 0) + (audio.outputLatency || 0)) * 1000)) : 0;
          if (outputTail > 0) captureEnableTimer = window.setTimeout(enableCapture, outputTail);
          else enableCapture();
          // A silent worklet can be the only observable signal when an OS
          // audio route pauses. Do not pause the worklet itself in that case:
          // its first fresh frame is how we discover recovery.
          captureWatch = window.setInterval(() => {
            if (!ended && captureEnabled && !endingRequested && !microphoneMuted && !deviceMuted && !captureInterrupted && performance.now() - lastCapture > 5000) {
              captureInterrupted = true;
              syncCapture();
            }
          }, 1000);
          updatePage();
          window.clearTimeout(deadline);
          if (document.activeElement === startButton.current) focusNext.current = "live";
          setState("live");
          if (socket?.readyState === WebSocket.OPEN) {
            try { socket.send(JSON.stringify({ type: "opening" })); }
            catch { fail("The voice call ended."); }
          }
        }
        if (event.type === "call_ending" && ready && !endingRequested) {
          endingRequested = true;
          stopRingback();
          setEndingCall(true);
          // Even if the final relay frame is lost, a confirmed hang-up cannot leave the UI live.
          window.clearTimeout(endTimer);
          endTimer = window.setTimeout(finishRequestedEnd, 9000);
          // Stop transmitting/capturing immediately. Keep only the short
          // farewell playing until the final relay event or socket close.
          for (const track of media?.getAudioTracks() ?? []) track.enabled = false;
          captureGeneration += 1;
          capture?.port.postMessage({ muted: true, generation: captureGeneration });
        }
        if (event.type === "call_ended" && ready) {
          endingRequested = true;
          stopRingback();
          setEndingCall(true);
          window.clearTimeout(endTimer);
          endTimer = window.setTimeout(finishRequestedEnd, Math.min(3000, playback?.remainingMs() ?? 0));
        }
        if (event.type === "error") fail(event.message || "The voice call ended.");
      };
      socket.onerror = () => endingRequested ? finishRequestedEnd() : fail("The voice call could not connect.");
      socket.onclose = () => endingRequested ? finishRequestedEnd() : fail("The voice call ended.");
    } catch {
      fail("Could not start live voice. Check your microphone permission and model settings.");
    }
  }

  if (state === "idle" && (!eligible || (activeVoice && activeVoice.owner !== owner.current))) return null;
  const idle = state === "idle";
  const live = state === "live";
  const action = idle ? "Start voice call" : live ? "End call" : "Cancel voice call";
  const microphoneAction = muted ? "Unmute microphone" : "Mute microphone";
  const liftAboveTooltip = "group-hover/voice:-translate-y-8 group-focus-within/voice:-translate-y-8";
  return <div className="group/voice relative inline-flex shrink-0 items-center gap-1 hover:z-20 focus-within:z-20">
    {live ? (
      <Surface role="group" aria-label="Live voice call controls" tone="panel-2" radius="pill"
        border="default" padding="none" className="inline-flex max-w-full shrink-0 items-center gap-0.5 p-0.5">
        <div role="status" className="composer-call-status inline-flex items-center gap-1 pl-2 pr-0.5">
          <AudioLines size={14} aria-hidden />
          <span className="sr-only">{endingCall ? "Ending voice call" : muted ? "Voice call live · Mic muted" : "Voice call live"}</span>
          <Text as="span" variant="body-sm" aria-hidden="true" className="composer-action-label">{endingCall ? "Ending" : "Live"}</Text>
        </div>
        <HoverTooltip label={microphoneAction} placement="top" dismissOnPress>
          <Button type="button" variant={muted ? "secondary" : "ghost"} size="xs" radius="pill"
            className="voice-call-control h-9 w-9 !p-0" disabled={endingCall} aria-label={microphoneAction}
            icon={muted
              ? <MicOff size={17} aria-hidden className="text-[var(--danger-fg)]" />
              : <Mic size={17} aria-hidden />}
            onClick={() => call.current?.toggleMicrophone()} />
        </HoverTooltip>
        <span className="composer-end-call inline-flex">
          <HoverTooltip label="End call" placement="top">
            <Button ref={endButton} type="button" variant="danger" size="xs" radius="pill" aria-label="End call"
              className="voice-call-control h-9 min-w-9 whitespace-nowrap px-2"
              icon={<Square size={12} aria-hidden />}
              onClick={() => {
                if (document.activeElement === endButton.current) focusNext.current = "idle";
                call.current?.close();
                setState("idle");
              }}>
              <span className="composer-action-label">End call</span>
            </Button>
          </HoverTooltip>
        </span>
      </Surface>
    ) : (
      <>
        {!idle && <Surface role="status" radius="input" elevation="sm" padding="xs"
          className={`absolute bottom-full right-0 z-10 mb-2 inline-flex w-max items-center gap-2 ${liftAboveTooltip}`}>
          <Loader2 size={14} aria-hidden className="motion-safe:animate-spin" />
          <Text as="span" variant="body-sm">Connecting voice…</Text>
        </Surface>}
        <HoverTooltip label={action} placement="top">
          <Button ref={startButton} type="button" variant={idle ? "ghost" : "danger"} size="xs" radius="pill"
            className="voice-call-control h-10 w-10 !p-0"
            aria-label={action}
            icon={idle ? <AudioLines size={17} aria-hidden /> : <Square size={12} aria-hidden />}
            onClick={() => {
              if (idle) void start();
              else { call.current?.close(); setState("idle"); }
            }} />
        </HoverTooltip>
      </>
    )}
    {live && audioInterrupted && !muted && <Surface role="status" radius="input" elevation="sm" padding="sm"
      className={`absolute bottom-full right-0 z-10 mb-2 w-56 ${liftAboveTooltip}`}><Text as="p" variant="body-sm">Audio interrupted. Check your microphone or audio connection. The call is still open.</Text></Surface>}
    {error && <Surface role="status" radius="input" elevation="sm" padding="sm"
      className={`absolute bottom-full right-0 z-10 mb-2 w-56 ${liftAboveTooltip}`}><Text as="p" variant="body-sm">{error}</Text></Surface>}
  </div>;
}

export function NativeVoiceCall({ home = false }: { home?: boolean }) {
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
  return <NativeVoiceControls surface={home ? "home" : "dock"} eligible={eligible}
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
      requestOpenCompanionChat({ threadId, navigating: true, workspaceId: workspace, principalId: user });
      navigate(path);
    }}
    opening={{ workspace_id: workspace, profile_key: profile, thread_id: activity.activeThreadId ?? "" }}
    onThreadReady={activity.adoptNativeVoiceThread}
    onThreadRefresh={activity.refreshNativeVoiceThread}
    playingDictation={() => dictation?.isListening() ?? false}
    acquireVoiceInput={dictation?.acquireVoiceInput}
    dictationActive={Boolean(dictationStatus && ["requesting-permission", "connecting", "listening", "finalizing"].includes(dictationStatus))} />;
}
