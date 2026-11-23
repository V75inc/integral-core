import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const track = { stop: vi.fn(), enabled: true };
const stopTrack = track.stop;
const createPlaybackBuffer = vi.fn();
const startPlayback = vi.fn();
const capturePorts: FakeWorklet["port"][] = [];

function emitCapture(port: FakeWorklet["port"], pcm: ArrayBuffer, generation = 0) {
  port.onmessage?.({ data: { generation, pcm } });
}

class FakeAudioContext {
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() }; }
  createBuffer = createPlaybackBuffer;
  createBufferSource() {
    return { connect: vi.fn(), disconnect: vi.fn(), start: startPlayback, stop: vi.fn(), onended: null };
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
  sockets.length = 0;
  capturePorts.length = 0;
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
  expect(screen.queryByText("End call", { selector: "[role=tooltip]" })).not.toBeInTheDocument();
  expect(screen.getAllByText("End call")).toHaveLength(1);
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
  act(() => { socket.open(); socket.receive("ready"); });
  const port = capturePorts[0];
  const frame = new ArrayBuffer(16);
  const mutedFrame = new ArrayBuffer(16);
  const mute = screen.getByRole("button", { name: "Mute microphone" });
  expect(screen.getByText("Mute microphone", { selector: "[role=tooltip]" })).toBeInTheDocument();
  act(() => emitCapture(port, frame));
  expect(socket.send).toHaveBeenNthCalledWith(3, frame);

  mute.focus();
  fireEvent.click(mute);
  expect(track.enabled).toBe(false);
  expect(screen.getByRole("button", { name: "Unmute microphone" })).toHaveFocus();
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
