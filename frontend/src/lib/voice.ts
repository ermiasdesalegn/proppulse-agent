// Voice notes recorded in the browser. They are sent as 16 kHz mono WAV: every browser can make it (unlike WebM or MP4, which differ by
// browser) and the transcription model reads it directly. A minute is about 1.9 MB.

export const VOICE_RATE = 16_000;
export const MAX_VOICE_SECONDS = 90;

/** Mixes recorded chunks into one 16-bit PCM WAV file. Samples outside -1..1 are clipped. */
export function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);          // size of the fmt chunk
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // bytes per second
  view.setUint16(32, 2, true);           // bytes per sample
  view.setUint16(34, 16, true);          // bits per sample
  text(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export interface Recording {
  stop: () => Promise<Blob>;
  cancel: () => void;
}

/** Starts recording from the microphone. The browser asks the buyer for permission the first time. */
export async function startRecording(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const context = new Ctx({ sampleRate: VOICE_RATE });
  const source = context.createMediaStreamSource(stream);
  // ScriptProcessor is old but works in every browser without a separate worklet file; a voice note needs nothing more.
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  processor.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(processor);
  processor.connect(context.destination);

  const close = () => {
    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    void context.close();
  };
  return {
    async stop() {
      close();
      return encodeWav(chunks, context.sampleRate);
    },
    cancel: close,
  };
}
