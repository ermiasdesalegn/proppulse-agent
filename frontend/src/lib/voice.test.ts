import { describe, expect, it } from "vitest";
import { encodeWav, VOICE_RATE } from "./voice";

describe("voice notes", () => {
  it("are sent as a 16 kHz mono 16-bit WAV file", async () => {
    const blob = encodeWav([new Float32Array([0, 0.5]), new Float32Array([-1, 2])], VOICE_RATE);
    const view = new DataView(await blob.arrayBuffer());
    const text = (at: number) => String.fromCharCode(...new Uint8Array(view.buffer, at, 4));
    expect(blob.type).toBe("audio/wav");
    expect([text(0), text(8), text(12), text(36)]).toEqual(["RIFF", "WAVE", "fmt ", "data"]);
    expect([view.getUint16(22, true), view.getUint32(24, true), view.getUint16(34, true)]).toEqual([1, 16_000, 16]);
    expect(view.getUint32(40, true)).toBe(8);
    expect([0, 1, 2, 3].map((i) => view.getInt16(44 + i * 2, true))).toEqual([0, 16383, -32768, 32767]); // the last sample is clipped
  });
});
