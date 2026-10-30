import { describe, expect, it } from "vitest";
import { chatFiles, chatPins, mergeMessages, newestId, stillWaiting, type Unconfirmed, type WebMessage } from "./chat";

const msg = (id: number, role: WebMessage["role"], content: string): WebMessage => ({ id, role, source: "text", content, created_at: "2026-11-01T08:00:00Z" });
const pending = (content: string, after: number, key = 1): Unconfirmed => ({ key, content, at: "2026-11-01T08:00:00Z", after });

describe("mergeMessages", () => {
  it("adds only new messages, in order, and keeps the same list when nothing is new", () => {
    const shown = [msg(1, "buyer", "hi"), msg(2, "agent", "hello")];
    expect(mergeMessages(shown, [msg(2, "agent", "hello")])).toBe(shown);
    expect(mergeMessages(shown, [msg(4, "broker", "d"), msg(3, "buyer", "c")]).map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });
});

describe("stillWaiting", () => {
  it("hides a sent message once its stored copy has arrived", () => {
    expect(stillWaiting([msg(5, "buyer", "3 bedrooms in Bole")], [pending("3 bedrooms in Bole", 4)])).toEqual([]);
  });

  it("keeps showing it while the stored copy has not arrived", () => {
    expect(stillWaiting([msg(4, "agent", "hello")], [pending("3 bedrooms in Bole", 4)])).toHaveLength(1);
  });

  it("does not mistake an older identical message for the new one", () => {
    const older = msg(2, "buyer", "yes");
    expect(stillWaiting([older], [pending("yes", 7)])).toHaveLength(1);
    expect(stillWaiting([older, msg(8, "buyer", "yes")], [pending("yes", 7)])).toEqual([]);
  });

  it("lets two identical sends each claim their own stored copy", () => {
    const stored = [msg(8, "buyer", "ok")];
    const two = [pending("ok", 7, 1), pending("ok", 7, 2)];
    expect(stillWaiting(stored, two).map((p) => p.key)).toEqual([2]);
    expect(stillWaiting([...stored, msg(9, "buyer", "ok")], two)).toEqual([]);
  });

  it("never matches the assistant's or the broker's words", () => {
    expect(stillWaiting([msg(9, "agent", "ok")], [pending("ok", 7)])).toHaveLength(1);
  });
});

describe("newestId", () => {
  it("is zero for an empty chat and the highest id otherwise", () => {
    expect(newestId([])).toBe(0);
    expect(newestId([msg(3, "buyer", "a"), msg(9, "agent", "b"), msg(5, "buyer", "c")])).toBe(9);
  });
});

describe("chatFiles", () => {
  it("keeps pictures and downloads served from the buyer's own message route", () => {
    const files = [
      { kind: "image" as const, url: "/api/me/chat/messages/7/model.png", label: "3D view: Bole" },
      { kind: "download" as const, url: "/api/me/chat/messages/7/model.glb", label: "Download 3D model (.glb)" },
    ];
    expect(chatFiles({ ...msg(7, "agent", "hi"), attachments: files })).toEqual(files);
  });

  it("drops anything pointing elsewhere, and copes with a message that has none", () => {
    const odd = [
      { kind: "download" as const, url: "https://evil.example/x.pdf", label: "x" },
      { kind: "image" as const, url: "/api/proxy/buyers", label: "x" },
      { kind: "download" as const, url: "/api/me/chat/messages/7/../../saved", label: "x" },
    ];
    expect(chatFiles({ ...msg(7, "agent", "hi"), attachments: odd })).toEqual([]);
    expect(chatFiles(msg(8, "agent", "hi"))).toEqual([]);
  });
});

describe("map pins in a message", () => {
  const base = { id: 9, role: "agent" as const, source: "text" as const, content: "Your visit is tomorrow", created_at: "2026-11-02T08:00:00Z" };

  it("opens the first stop in the maps app, built from the coordinates alone", () => {
    expect(chatPins({ ...base, attachments: [{ kind: "map", lat: 8.995, lng: 38.79, label: "Map: Bole 3BR" }] }))
      .toEqual([{ href: "https://www.google.com/maps/search/?api=1&query=8.995000,38.790000", label: "Map: Bole 3BR" }]);
  });

  it("ignores pins without real coordinates", () => {
    const odd = [{ kind: "map", lat: 120, lng: 38, label: "x" }, { kind: "map", lat: Number.NaN, lng: 1, label: "y" }] as never;
    expect(chatPins({ ...base, attachments: odd })).toEqual([]);
  });
});
