import { describe, expect, it } from "vitest";
import { sortVisits, visitTitle, type Visit } from "./visits";

const v = (id: string, at: string, status: Visit["status"]): Visit => ({ id, scheduled_start: at, kind: "SITE_VISIT", status, can_cancel: status === "upcoming", homes: [], project: null });

describe("visits", () => {
  it("names a visit by its homes, project or kind", () => {
    expect(visitTitle({ kind: "CONSULTATION", homes: [], project: null })).toBe("Phone consultation");
    expect(visitTitle({ kind: "SITE_VISIT", homes: ["Bole 3BR"], project: null })).toBe("Bole 3BR");
    expect(visitTitle({ kind: "SITE_VISIT", homes: ["Bole 3BR", "Ayat 2BR", "CMC"], project: null })).toBe("Bole 3BR and 2 more");
    expect(visitTitle({ kind: "SITE_VISIT", homes: [], project: "Noah Towers" })).toBe("Visit to Noah Towers");
    expect(visitTitle({ kind: "SITE_VISIT", homes: [], project: null })).toBe("Site visit");
  });
  it("lists upcoming visits soonest first, then the rest newest first", () => {
    const sorted = sortVisits([v("old", "2026-01-01T09:00:00Z", "past"), v("late", "2027-01-02T09:00:00Z", "upcoming"), v("gone", "2026-06-01T09:00:00Z", "cancelled"), v("soon", "2027-01-01T09:00:00Z", "upcoming")]);
    expect(sorted.map((x) => x.id)).toEqual(["soon", "late", "gone", "old"]);
  });
});
