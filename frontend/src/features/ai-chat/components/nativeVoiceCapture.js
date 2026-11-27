/* global AudioWorkletProcessor, registerProcessor */

class NativeVoiceCapture extends AudioWorkletProcessor {
  constructor(options) {
    super(options);
    this.buffer = new Int16Array(480);
    this.offset = 0;
    this.muted = options?.processorOptions?.muted === true;
    this.generation = 0;
    this.port.onmessage = ({ data: { muted, generation } }) => {
      this.offset = 0;
      this.muted = muted;
      this.generation = generation;
    };
  }

  process(inputs) {
    if (this.muted) return true;
    for (const sample of inputs[0]?.[0] ?? []) {
      this.buffer[this.offset++] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
      if (this.offset === this.buffer.length) {
        const bytes = this.buffer.buffer;
        this.port.postMessage({ generation: this.generation, pcm: bytes }, [bytes]);
        this.buffer = new Int16Array(480);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("integral-native-voice", NativeVoiceCapture);
