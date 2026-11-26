import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NativeVoiceControls } from "../NativeVoiceCall";

vi.mock("../../AIChatSurface", () => ({ useChatActivity: vi.fn() }));
vi.mock("../../../../api/wsTicket", () => ({ mintWsTicket: vi.fn().mockResolvedValue("test-ticket") }));

class FakeSocket {
  static OPEN = 1;
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string | ArrayBuffer }) => void) | null = null;
  onerror = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; });

  constructor() { sockets.push(this); }
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  receive(type: string) { this.onmessage?.({ data: JSON.stringify({ type }) }); }
}

const sockets: FakeSocket[] = [];

it("opens from a thread deep link and acknowledges only the rendered destination", async () => {
  const navigate = vi.fn();
  let rendered = false;
  const props = {
    eligible: true,
    opening: { workspace_id: "workspace", profile_key: "", thread_id: "thread" },
    playingDictation: () => false,
    onNavigate: navigate,
    destinationRendered: () => rendered,
  };
  const view = render(<NativeVoiceControls {...props} pageRoute="/agent?thread=thread" />);
  const socket = await startCall();
  act(() => {
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "ready", thread_id: "thread" }) });
  });
  const path = "/apps/n.WorkspaceApp.example";
  const event = { type: "navigation", id: "one-use", origin_revision: 1,
    navigation: { path, thread_id: "thread", workspace_id: "workspace" } };
  act(() => socket.onmessage?.({ data: JSON.stringify(event) }));
  expect(navigate).toHaveBeenCalledWith(path, "thread");
  expect(socket.send).not.toHaveBeenCalledWith(expect.stringContaining("navigation_result"));
  rendered = true;
  view.rerender(<NativeVoiceControls {...props} pageRoute={path} />);
  await waitFor(() => expect(socket.send).toHaveBeenCalledWith(JSON.stringify({
    type: "navigation_result", id: "one-use", status: "rendered",
  })));
  expect(screen.getByRole("button", { name: "End call" })).toBeVisible();
  act(() => socket.onmessage?.({ data: JSON.stringify({ ...event, id: "late" }) }));
  expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({
    type: "navigation_result", id: "late", status: "preserved",
  }));
});
const track = Object.assign(new EventTarget(), { stop: vi.fn(), enabled: true });
const stopTrack = track.stop;
const createPlaybackBuffer = vi.fn();
const startPlayback = vi.fn();
const playbackSources: { onended: (() => void) | null }[] = [];
const capturePorts: FakeWorklet["port"][] = [];

function emitCapture(port: FakeWorklet["port"], pcm: ArrayBuffer, generation = 0) {
  port.onmessage?.({ data: { generation, pcm } });
}

class FakeAudioContext {
  static time = 0;
  get currentTime() { return FakeAudioContext.time; }
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() }; }
  createBuffer = createPlaybackBuffer;
  decodeAudioData = vi.fn().mockResolvedValue({ duration: 1 });
  createBufferSource() {
    const source = { connect: vi.fn(), disconnect: vi.fn(), start: startPlayback, stop: vi.fn(), onended: null as (() => void) | null };
    playbackSources.push(source);
    return source;
  }
}

class FakeWorklet {
  port = {
    onmessage: null as ((frame: { data: { generation: number; pcm: ArrayBuffer } }) => void) | null,
    postMessage: vi.fn(),
  };
  disconnect = vi.fn();
  constructor() { capturePorts.push(this.port); }
}

const mediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
beforeEach(() => {
  FakeAudioContext.time = 0;
  sockets.length = 0;
  capturePorts.length = 0;
  playbackSources.length = 0;
  vi.clearAllMocks();
  track.enabled = true;
  createPlaybackBuffer.mockImplementation((_channels, samples: number) => ({
    duration: samples / 24000,
    getChannelData: () => new Float32Array(samples),
  }));
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeWorklet);
  vi.stubGlobal("WebSocket", FakeSocket);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track], getAudioTracks: () => [track] }) },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  if (mediaDevices) Object.defineProperty(navigator, "mediaDevices", mediaDevices);
  else Reflect.deleteProperty(navigator, "mediaDevices");
});

function show() {
  return render(<NativeVoiceControls eligible opening={{
    workspace_id: "workspace", profile_key: "staff", thread_id: "thread",
  }} playingDictation={() => false} />);
}

async function startCall(index = 0) {
  fireEvent.click(screen.getByRole("button", { name: "Start voice call" }));
  expect(screen.getByRole("status")).toHaveTextContent("Connecting voice");
  await waitFor(() => expect(sockets).toHaveLength(index + 1));
  return sockets[index];
}

it("uses a waveform and waits for provider readiness before requesting one opening", async () => {
  show();
  const start = screen.getByRole("button", { name: "Start voice call" });
  expect(start.querySelector(".lucide-audio-lines")).not.toBeNull();
  expect(screen.getByText("Start voice call", { selector: "[role=tooltip]" })).toHaveAttribute("aria-hidden", "true");
  expect(start).not.toHaveAttribute("title");
  start.focus();
  const socket = await startCall();
  expect(screen.getByRole("button", { name: "Cancel voice call" })).toHaveFocus();
  expect(screen.getByText("Cancel voice call", { selector: "[role=tooltip]" })).toBeInTheDocument();
  expect(screen.queryByText("End call")).not.toBeInTheDocument();
  act(() => socket.open());
  expect(socket.send).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("test-ticket"));
  const frame = new ArrayBuffer(16);
  act(() => { emitCapture(capturePorts[0], frame); socket.onmessage?.({ data: frame }); });
  expect(socket.send).toHaveBeenCalledTimes(1);
  expect(createPlaybackBuffer).not.toHaveBeenCalled();

  act(() => socket.receive("ready"));
  expect(screen.getByRole("status")).toHaveTextContent("Voice call live");
  expect(screen.getByText("End call", { selector: "[role=tooltip]" })).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("button", { name: "End call" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Cancel voice call" })).not.toBeInTheDocument();
  expect(socket.send).toHaveBeenNthCalledWith(2, JSON.stringify({ type: "opening" }));
  act(() => {
    socket.receive("ready");
    socket.receive("flush"); // A speech interruption or Heavy handoff is not a new call.
    emitCapture(capturePorts[0], frame);
  });
  expect(socket.send).toHaveBeenCalledTimes(3);
  expect(socket.send).toHaveBeenNthCalledWith(3, frame);
  fireEvent.click(screen.getByRole("button", { name: "End call" }));
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(stopTrack).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Start voice call" })).toHaveFocus();
});

it("sends no opening for an ended connection and starts a fresh opening only on a new deliberate call", async () => {
  show();
  const first = await startCall();
  act(() => first.open());
  const staleMessage = first.onmessage;
  fireEvent.click(screen.getByRole("button", { name: "Cancel voice call" }));
  act(() => staleMessage?.({ data: JSON.stringify({ type: "ready" }) }));
  expect(first.send).toHaveBeenCalledTimes(1);
  expect(sockets).toHaveLength(1);

  const second = await startCall(1);
  act(() => { second.open(); second.receive("ready"); second.receive("ready"); });
  expect(second.send).toHaveBeenCalledTimes(2);
  expect(second.send).toHaveBeenNthCalledWith(2, JSON.stringify({ type: "opening" }));
  act(() => second.onclose?.());
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeVisible();
  expect(sockets).toHaveLength(2);
});


it("adopts the durable ready thread without hanging up and reuses it on the next start", async () => {
  const adopt = vi.fn().mockResolvedValue(undefined);
  const refresh = vi.fn();
  const controls = (thread_id: string) => <NativeVoiceControls eligible opening={{
    workspace_id: "workspace", profile_key: "staff", thread_id,
  }} playingDictation={() => false} onThreadReady={adopt} onThreadRefresh={refresh} />;
  const view = render(controls(""));
  expect(adopt).not.toHaveBeenCalled();
  const first = await startCall();
  act(() => first.open());
  expect(adopt).not.toHaveBeenCalled();
  act(() => first.onmessage?.({ data: JSON.stringify({ type: "ready", thread_id: "voice-thread" }) }));
  expect(adopt).toHaveBeenCalledExactlyOnceWith("voice-thread", {
    workspace_id: "workspace", profile_key: "staff", thread_id: "",
  });
  view.rerender(controls("voice-thread"));
  expect(first.close).not.toHaveBeenCalled();
  expect(screen.getByText("Voice call live")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "End call" }));
  expect(refresh).toHaveBeenCalledWith("voice-thread", "workspace");
  const next = await startCall(1);
  act(() => next.open());
  expect(JSON.parse(next.send.mock.calls[0][0])).toMatchObject({ thread_id: "voice-thread" });
});

it("never adopts a thread when voice fails before ready", async () => {
  const adopt = vi.fn();
  render(<NativeVoiceControls eligible opening={{ workspace_id: "workspace", profile_key: "", thread_id: "" }}
    playingDictation={() => false} onThreadReady={adopt} />);
  const first = await startCall();
  act(() => { first.open(); first.receive("error"); });
  expect(adopt).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeVisible();
});


it("mutes socket input immediately, drops stale capture frames on resume, and keeps playback live", async () => {
  show();
  const socket = await startCall();
  expect(screen.queryByRole("button", { name: "Mute microphone" })).not.toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Live voice call controls" })).not.toBeInTheDocument();
  act(() => { socket.open(); socket.receive("ready"); });
  const port = capturePorts[0];
  const frame = new ArrayBuffer(16);
  const mutedFrame = new ArrayBuffer(16);
  const callWidget = screen.getByRole("group", { name: "Live voice call controls" });
  const mute = within(callWidget).getByRole("button", { name: "Mute microphone" });
  expect(mute.textContent).toBe("");
  expect(mute.querySelector(".lucide-mic")).not.toBeNull();
  expect(within(callWidget).getByRole("button", { name: "End call" })).toBeVisible();
  expect(within(callWidget).getByRole("status")).toHaveTextContent("Voice call live");
  expect(screen.getByText("Mute microphone", { selector: "[role=tooltip]" })).toBeInTheDocument();
  act(() => emitCapture(port, frame));
  expect(socket.send).toHaveBeenNthCalledWith(3, frame);

  mute.focus();
  fireEvent.click(mute);
  expect(track.enabled).toBe(false);
  const unmute = within(callWidget).getByRole("button", { name: "Unmute microphone" });
  expect(unmute).toHaveFocus();
  expect(screen.queryByText("Unmute microphone", { selector: "[role=tooltip]" })).toBeNull();
  fireEvent.pointerEnter(unmute);
  expect(screen.getByText("Unmute microphone", { selector: "[role=tooltip]" })).toBeInTheDocument();
  expect(unmute.textContent).toBe("");
  expect(unmute.querySelector(".lucide-mic-off")).toHaveClass("text-[var(--danger-fg)]");
  expect(screen.getByRole("status")).toHaveTextContent("Mic muted");
  expect(port.postMessage).toHaveBeenNthCalledWith(1, { muted: true, generation: 1 });
  expect(socket.send).toHaveBeenNthCalledWith(4, JSON.stringify({ type: "microphone", muted: true }));
  act(() => {
    emitCapture(port, frame); // Already queued before mute.
    emitCapture(port, mutedFrame, 1);
    socket.onmessage?.({ data: frame });
  });
  expect(socket.send).toHaveBeenCalledTimes(4);
  expect(startPlayback).toHaveBeenCalledTimes(1);
  expect(socket.close).not.toHaveBeenCalled();
  expect(stopTrack).not.toHaveBeenCalled();

  let trackEnabledAtUnmuteSend: boolean | undefined;
  socket.send.mockImplementationOnce(() => { trackEnabledAtUnmuteSend = track.enabled; });
  fireEvent.click(screen.getByRole("button", { name: "Unmute microphone" }));
  expect(trackEnabledAtUnmuteSend).toBe(false);
  expect(track.enabled).toBe(true);
  expect(port.postMessage).toHaveBeenNthCalledWith(2, { muted: false, generation: 2 });
  expect(socket.send).toHaveBeenNthCalledWith(5, JSON.stringify({ type: "microphone", muted: false }));
  act(() => { emitCapture(port, frame); emitCapture(port, mutedFrame, 1); });
  expect(socket.send).toHaveBeenCalledTimes(5);
  act(() => emitCapture(port, frame, 2));
  expect(socket.send).toHaveBeenNthCalledWith(6, frame);

  const staleCapture = port.onmessage;
  fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
  fireEvent.click(screen.getByRole("button", { name: "End call" }));
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(stopTrack).toHaveBeenCalledTimes(1);
  expect(port.onmessage).toBeNull();
  act(() => staleCapture?.({ data: { generation: 3, pcm: frame } }));
  expect(socket.send).toHaveBeenCalledTimes(7);

  // A new getUserMedia call has fresh, enabled tracks.
  track.enabled = true;
  const next = await startCall(1);
  act(() => { next.open(); next.receive("ready"); });
  expect(screen.getByRole("button", { name: "Mute microphone" })).toBeVisible();
  act(() => emitCapture(capturePorts[1], frame));
  expect(next.send).toHaveBeenNthCalledWith(3, frame);
});

it("ends the call and releases capture even if the final chat refresh throws", async () => {
  let cleanedUpBeforeRefresh = false;
  const refresh = vi.fn(() => {
    cleanedUpBeforeRefresh = sockets[0].close.mock.calls.length === 1 && stopTrack.mock.calls.length === 1;
    throw new Error("The view is no longer available");
  });
  render(<NativeVoiceControls eligible opening={{
    workspace_id: "workspace", profile_key: "", thread_id: "thread",
  }} playingDictation={() => false} onThreadRefresh={refresh} />);
  const socket = await startCall();
  act(() => { socket.open(); socket.receive("ready"); });
  fireEvent.click(screen.getByRole("button", { name: "End call" }));
  expect(refresh).toHaveBeenCalledExactlyOnceWith("thread", "workspace");
  expect(cleanedUpBeforeRefresh).toBe(true);
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeVisible();
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

it("claims dictation for the entire connection and releases it on hangup or failure, not mute", async () => {
  const release = vi.fn();
  const acquire = vi.fn(() => release);
  render(<NativeVoiceControls eligible opening={{
    workspace_id: "workspace", profile_key: "staff", thread_id: "thread",
  }} playingDictation={() => false} acquireVoiceInput={acquire} />);
  const first = await startCall();
  expect(acquire).toHaveBeenCalledTimes(1);
  expect(release).not.toHaveBeenCalled();
  act(() => { first.open(); first.receive("ready"); });
  fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
  expect(release).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "End call" }));
  expect(release).toHaveBeenCalledTimes(1);

  const second = await startCall(1);
  expect(acquire).toHaveBeenCalledTimes(2);
  act(() => second.onclose?.());
  expect(release).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeEnabled();
});

it("releases voice and microphone on document pagehide without refreshing the dying view", async () => {
  const release = vi.fn();
  const refresh = vi.fn();
  render(<NativeVoiceControls eligible opening={{
    workspace_id: "workspace", profile_key: "staff", thread_id: "thread",
  }} playingDictation={() => false} acquireVoiceInput={() => release} onThreadRefresh={refresh} />);
  const socket = await startCall();
  act(() => {
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "ready", thread_id: "thread" }) });
  });
  const worklet = capturePorts[0];
  expect(screen.getByRole("button", { name: "End call" })).toBeVisible();
  act(() => window.dispatchEvent(new Event("pagehide")));
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(stopTrack).toHaveBeenCalledTimes(1);
  expect(release).toHaveBeenCalledTimes(1);
  expect(worklet.onmessage).toBeNull();
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeEnabled();
  act(() => window.dispatchEvent(new Event("pagehide")));
  expect(socket.close).toHaveBeenCalledTimes(1);
});

it("reports only queued samples when the caller interrupts playback", async () => {
  show();
  const socket = await startCall();
  act(() => {
    socket.open();
    socket.receive("ready");
    socket.onmessage?.({ data: JSON.stringify({ type: "playback_item", handle: 7 }) });
    socket.onmessage?.({ data: new ArrayBuffer(24000) });
    socket.onmessage?.({ data: new ArrayBuffer(24000) });
    // Playback started at 20ms. The first half-second has played.
    FakeAudioContext.time = 0.52;
    socket.receive("flush");
  });
  expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({
    type: "playback_interrupted", items: [[7, 12000]],
  }));
  const count = socket.send.mock.calls.length;
  act(() => socket.receive("flush"));
  expect(socket.send).toHaveBeenCalledTimes(count);
});


it("recovers when an active microphone is disconnected", async () => {
  show();
  const socket = await startCall();
  act(() => { socket.open(); socket.receive("ready"); });
  act(() => track.dispatchEvent(new Event("ended")));
  expect(screen.getByRole("button", { name: "Start voice call" })).toBeVisible();
  expect(screen.getByText("The microphone was disconnected. Reconnect it and start a new call.")).toBeVisible();
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

it.each(["finish", "interrupt"] as const)("uses only verified call audio during review (%s)", async (action) => {
  const speak = vi.fn();
  vi.stubGlobal("speechSynthesis", { getVoices: () => [{ lang: "en-US", localService: true }], speak });
  show();
  const socket = await startCall();
  act(() => { socket.open(); socket.receive("ready"); });
  const send = (data: object) => act(() => socket.onmessage?.({ data: JSON.stringify(data) }));
  send({ type: "review_confirmation", id: "fixture" });
  send({ type: "review_confirmation_audio", id: "stale", audio: btoa("stale-WAV") });
  expect(startPlayback).not.toHaveBeenCalled();
  send({ type: "review_confirmation_audio", id: "fixture", audio: btoa("fixture-WAV") });
  await waitFor(() => expect(startPlayback).toHaveBeenCalledTimes(1));
  const silent = new ArrayBuffer(16);
  act(() => emitCapture(capturePorts[0], silent));
  expect(socket.send).toHaveBeenCalledTimes(2);
  if (action === "finish") {
    act(() => playbackSources[0].onended?.());
    expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({ type: "review_prompt_finished", id: "fixture" }));
    act(() => emitCapture(capturePorts[0], silent, 1));
    expect(socket.send).toHaveBeenLastCalledWith(silent);
  } else {
    const voice = new Int16Array(480).fill(6000).buffer;
    act(() => { for (let index = 0; index < 8; index++) emitCapture(capturePorts[0], voice); });
    expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({ type: "review_prompt_interrupted", id: "fixture" }));
    expect(socket.send).not.toHaveBeenCalledWith(voice);
  }
  expect(speak).not.toHaveBeenCalled();
});

it("opens a voice call without forwarding a legacy browser permission", async () => {
  localStorage.setItem(JSON.stringify(["voice-create-list", "one", "workspace"]), "n.Track.tasks");
  render(<NativeVoiceControls eligible opening={{workspace_id:"workspace",profile_key:"",thread_id:"thread"}} playingDictation={() => false} />);
  const socket = await startCall();
  act(() => socket.open());
  expect(JSON.parse(socket.send.mock.calls[0][0])).not.toHaveProperty("direct_track");
  localStorage.clear();
});
