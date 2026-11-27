import { expect, it, vi } from "vitest";
import processorSource from "../nativeVoiceCapture.js?raw";

it("discards partial PCM at mute boundaries and emits only fresh capture after unmute", () => {
  const port = {
    onmessage: null as ((event: { data: { muted: boolean; generation: number } }) => void) | null,
    postMessage: vi.fn(),
  };
  class FakeAudioWorkletProcessor {
    port = port;
  }
  const register = vi.fn<(name: string, ctor: new () => { process: (inputs: Float32Array[][]) => boolean }) => void>();
  new Function("AudioWorkletProcessor", "registerProcessor", processorSource)(FakeAudioWorkletProcessor, register);
  expect(register).toHaveBeenCalledExactlyOnceWith("integral-native-voice", expect.any(Function));
  const capture = new register.mock.calls[0][1]();

  capture.process([[new Float32Array(240).fill(0.5)]]);
  expect(port.postMessage).not.toHaveBeenCalled();
  port.onmessage?.({ data: { muted: true, generation: 1 } });
  capture.process([[new Float32Array(960).fill(1)]]);
  expect(port.postMessage).not.toHaveBeenCalled();

  port.onmessage?.({ data: { muted: false, generation: 2 } });
  capture.process([[new Float32Array(240).fill(-1)]]);
  expect(port.postMessage).not.toHaveBeenCalled();
  capture.process([[new Float32Array(240).fill(-1)]]);
  const frame = port.postMessage.mock.calls[0][0];
  expect(port.postMessage).toHaveBeenCalledExactlyOnceWith({ generation: 2, pcm: frame.pcm }, [frame.pcm]);
  expect(new Int16Array(frame.pcm)).toEqual(new Int16Array(480).fill(-32767));
});


it("can start muted so connecting and ringback samples never enter the PCM stream", () => {
  const port = { onmessage: null as ((event: { data: { muted: boolean; generation: number } }) => void) | null, postMessage: vi.fn() };
  class FakeAudioWorkletProcessor { port = port; }
  const register = vi.fn<(name: string, ctor: new (options: { processorOptions: { muted: boolean } }) => { process: (inputs: Float32Array[][]) => boolean }) => void>();
  new Function("AudioWorkletProcessor", "registerProcessor", processorSource)(FakeAudioWorkletProcessor, register);
  const capture = new register.mock.calls[0][1]({ processorOptions: { muted: true } });
  capture.process([[new Float32Array(960).fill(1)]]);
  expect(port.postMessage).not.toHaveBeenCalled();
  port.onmessage?.({ data: { muted: false, generation: 0 } });
  capture.process([[new Float32Array(480).fill(-0.5)]]);
  const frame = port.postMessage.mock.calls[0][0];
  expect(new Int16Array(frame.pcm)).toEqual(new Int16Array(480).fill(-16383));
});
