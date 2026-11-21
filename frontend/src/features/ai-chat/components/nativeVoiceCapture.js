class NativeVoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(480);
    this.offset = 0;
  }

  process(inputs) {
    for (const sample of inputs[0]?.[0] ?? []) {
      this.buffer[this.offset++] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
      if (this.offset === this.buffer.length) {
        const bytes = this.buffer.buffer;
        this.port.postMessage(bytes, [bytes]);
        this.buffer = new Int16Array(480);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("integral-native-voice", NativeVoiceCapture);
