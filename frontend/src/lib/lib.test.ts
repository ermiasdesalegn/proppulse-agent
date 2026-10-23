import { describe, expect, it } from "vitest";
import { COLUMNS, applyMove, canMove, dealsByStatus, expectedCommission, needsContractDetails, openPipeline } from "./board";
import { foldTurns, nodeTimings, describeNode } from "./events";
import { displayName, fmtDuration, fmtEtb, fmtPct, stageLabel, timeAgo } from "./format";
import { makeProjection, spreadPins } from "./geo";
import { decodePolyline, encodePolyline } from "./polyline";
import { backendUrl, forwardedHeaders, responseHeaders } from "./proxy";
import { RateLimiter } from "./ratelimit";
import { createSession, passwordMatches, safeEqual, verifySession } from "./session";
import type { BoardData, Deal, LiveEvent, NodeEvent } from "./types";

// ---------------- session ----------------

describe("session cookie", () => {
  const secret = "test-secret-value";
  it("accepts a fresh token and rejects tampering, expiry and a different secret", async () => {
    const now = 1_000_000;
    const token = await createSession(secret, 60, now);
    expect(await verifySession(token, secret, now + 1000)).toBe(true);
    expect(await verifySession(token, secret, now + 61_000)).toBe(false);
    expect(await verifySession(token, "another-secret", now + 1000)).toBe(false);
    const [exp, sig] = token.split(".");
    expect(await verifySession(`${Number(exp) + 999999}.${sig}`, secret, now + 1000)).toBe(false);
    expect(await verifySession(`${exp}.${sig.slice(0, -1)}0`, secret, now + 1000)).toBe(false);
  });
  it("rejects missing, malformed and unconfigured input", async () => {
    expect(await verifySession(undefined, secret)).toBe(false);
    expect(await verifySession("", secret)).toBe(false);
    expect(await verifySession("abc", secret)).toBe(false);
    expect(await verifySession("1.2.3", secret)).toBe(false);
    expect(await verifySession("9999999999999.abc", secret)).toBe(false);
    expect(await verifySession(await createSession(secret, 60), "")).toBe(false);
  });
  it("compares passwords without matching an empty one", async () => {
    expect(await passwordMatches("hunter2", "hunter2")).toBe(true);
    expect(await passwordMatches("hunter3", "hunter2")).toBe(false);
    expect(await passwordMatches("", "")).toBe(false);
    expect(await passwordMatches("anything", "")).toBe(false);
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });
});

describe("rate limiter", () => {
  it("allows the limit, blocks beyond it, and recovers after the window", () => {
    const limiter = new RateLimiter(3, 1000);
    expect([1, 2, 3, 4, 5].map(() => limiter.attempt("ip", 0))).toEqual([true, true, true, false, false]);
    expect(limiter.attempt("other-ip", 0)).toBe(true);
    expect(limiter.attempt("ip", 1001)).toBe(true);
    limiter.attempt("ip", 1002); limiter.reset("ip");
    expect(limiter.attempt("ip", 1003)).toBe(true);
  });
});

// ---------------- proxy ----------------

describe("proxy", () => {
  const base = "http://127.0.0.1:8000/";
  it("maps a path to the backend api and keeps the query", () => {
    expect(backendUrl(["buyers"], "", base)).toBe("http://127.0.0.1:8000/api/buyers");
    expect(backendUrl(["chat", "web"], "?x=1", base)).toBe("http://127.0.0.1:8000/api/chat/web?x=1");
  });
  it("refuses traversal, empty segments, encoded slashes and the Telegram webhook", () => {
    for (const bad of [[], [".."], ["a", ".."], [""], ["a%2Fb"], ["a\\b"], ["telegram", "webhook"], ["x", "."]]) {
      expect(backendUrl(bad, "", base)).toBeNull();
    }
  });
  it("forwards only what the backend needs and always adds the admin key", () => {
    const out = forwardedHeaders(new Headers({ "content-type": "application/json", cookie: "pp_session=secret", "last-event-id": "7", authorization: "x" }), "KEY");
    expect(out.get("x-admin-key")).toBe("KEY");
    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("last-event-id")).toBe("7");
    expect(out.get("cookie")).toBeNull();
    expect(out.get("authorization")).toBeNull();
  });
  it("never passes backend cookies back and unbuffers event streams", () => {
    const out = responseHeaders(new Headers({ "content-type": "text/event-stream", "set-cookie": "a=b", "content-length": "5" }));
    expect(out.get("set-cookie")).toBeNull();
    expect(out.get("cache-control")).toBe("no-cache, no-transform");
    expect(out.get("content-length")).toBeNull();
  });
});

// ---------------- formatting ----------------

describe("format", () => {
  it("formats money, percentages and durations", () => {
    expect(fmtEtb(14_500_000)).toBe("14.5M ETB");
    expect(fmtEtb(14_000_000)).toBe("14M ETB");
    expect(fmtEtb(850_000)).toBe("850,000 ETB");
    expect(fmtEtb(null)).toBe("-");
    expect(fmtPct(16.18)).toBe("16.2%");
    expect(fmtDuration(420)).toBe("420 ms");
    expect(fmtDuration(2500)).toBe("2.5 s");
  });
  it("names buyers by their best identifier", () => {
    expect(displayName({ full_name: "Abel", thread_id: "tg:1" })).toBe("Abel");
    expect(displayName({ full_name: null, thread_id: "tg:794655305" })).toBe("Telegram 794655305");
    expect(displayName({ full_name: null, thread_id: "web:abcdef123456" })).toBe("Web visitor abcdef");
    expect(stageLabel("INVENTORY_PENDING")).toBe("Inventory Pending");
  });
  it("describes recency", () => {
    const now = Date.parse("2026-11-02T12:00:00Z");
    expect(timeAgo("2026-11-02T11:59:50Z", now)).toBe("just now");
    expect(timeAgo("2026-11-02T11:30:00Z", now)).toBe("30 min ago");
    expect(timeAgo("2026-11-02T06:00:00Z", now)).toBe("6 h ago");
    expect(timeAgo("2026-10-30T12:00:00Z", now)).toBe("3 d ago");
  });
});

// ---------------- polyline and projection ----------------

describe("polyline", () => {
  it("decodes the reference example from the polyline algorithm documentation", () => {
    expect(decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")).toEqual([[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]);
  });
  it("round-trips Addis Ababa coordinates", () => {
    const pts: [number, number][] = [[9.0, 38.75], [9.0147, 38.7636], [8.995, 38.786], [9.0235, 38.869]];
    const back = decodePolyline(encodePolyline(pts));
    back.forEach((p, i) => { expect(p[0]).toBeCloseTo(pts[i][0], 5); expect(p[1]).toBeCloseTo(pts[i][1], 5); });
  });
  it("survives empty and truncated input", () => {
    expect(decodePolyline("")).toEqual([]);
    expect(decodePolyline("_p~iF~ps|U_ulL").length).toBeGreaterThanOrEqual(1);
  });
});

describe("map projection", () => {
  const pts: [number, number][] = [[8.95, 38.7], [9.05, 38.9]];
  it("keeps every point inside the padded box and puts north at the top", () => {
    const p = makeProjection(pts, 800, 600, 40);
    const sw = p.project(8.95, 38.7), ne = p.project(9.05, 38.9);
    for (const q of [sw, ne]) { expect(q.x).toBeGreaterThanOrEqual(39.9); expect(q.x).toBeLessThanOrEqual(760.1); expect(q.y).toBeGreaterThanOrEqual(39.9); expect(q.y).toBeLessThanOrEqual(560.1); }
    expect(ne.y).toBeLessThan(sw.y);
    expect(ne.x).toBeGreaterThan(sw.x);
  });
  it("handles a single point and identical points", () => {
    const p = makeProjection([[9, 38.8]], 400, 300);
    const q = p.project(9, 38.8);
    expect(Number.isFinite(q.x) && Number.isFinite(q.y)).toBe(true);
    expect(q.x).toBeCloseTo(200, 0); expect(q.y).toBeCloseTo(150, 0);
  });
});

describe("pin spreading", () => {
  const overlaps = (a: { cx: number; cy: number; w: number }, b: { cx: number; cy: number; w: number }) =>
    Math.abs(a.cx - b.cx) < (a.w + b.w) / 2 && Math.abs(a.cy - b.cy) < 22;
  it("separates pills that start on the same spot and flags the moved ones", () => {
    const placed = spreadPins([{ id: "a", x: 100, y: 100, w: 40 }, { id: "b", x: 100, y: 100, w: 40 }, { id: "c", x: 100, y: 100, w: 40 }, { id: "d", x: 105, y: 102, w: 50 }]);
    for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++) expect(overlaps(placed[i], placed[j])).toBe(false);
    expect(placed.filter((p) => p.moved).length).toBeGreaterThanOrEqual(3);
    expect(placed.find((p) => p.id === "a")!.moved).toBe(false);
  });
  it("leaves well separated pills exactly where they are", () => {
    const placed = spreadPins([{ id: "a", x: 50, y: 50, w: 40 }, { id: "b", x: 300, y: 200, w: 40 }]);
    expect(placed.every((p) => !p.moved && p.cy === (p.id === "a" ? 50 : 200))).toBe(true);
  });
  it("is deterministic and keeps every pin", () => {
    const input = Array.from({ length: 10 }, (_, i) => ({ id: `p${i}`, x: 100, y: 100, w: 44 }));
    expect(spreadPins(input)).toEqual(spreadPins([...input].reverse()));
    expect(spreadPins(input)).toHaveLength(10);
  });
});

// ---------------- board ----------------

const deal = (over: Partial<Deal> = {}): Deal => ({
  id: "d1", buyer_id: "b1", buyer_name: "Abel", developer_name: "Noah", developer_slug: "noah", property_title: "Unit", property_price_etb: 19_800_000,
  status: "LEAD_REGISTERED", expected_commission_etb: 495_000, contract_value_etb: null, next_buyer_milestone_date: null, lead_reg_email_id: "em",
  lead_score: 40, created_at: "", updated_at: "", ...over,
});

describe("deal board", () => {
  it("has the agreed columns in order, with only deal columns accepting drops", () => {
    expect(COLUMNS.map((c) => c.id)).toEqual(["QUALIFIED", "LEAD_REGISTERED", "TOUR_COMPLETED", "CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID", "CLOSED_LOST"]);
    expect(COLUMNS.filter((c) => !c.accepts).map((c) => c.id)).toEqual(["QUALIFIED"]);
  });
  it("groups deals by status and ignores unknown ones", () => {
    const grouped = dealsByStatus([deal(), deal({ id: "d2", status: "CLOSED_LOST" }), deal({ id: "d3", status: "WHATEVER" as never })]);
    expect(grouped.LEAD_REGISTERED.map((d) => d.id)).toEqual(["d1"]);
    expect(grouped.CLOSED_LOST.map((d) => d.id)).toEqual(["d2"]);
  });
  it("asks for details only when signing without a milestone date", () => {
    expect(needsContractDetails(deal(), "CONTRACT_SIGNED")).toBe(true);
    expect(needsContractDetails(deal({ next_buyer_milestone_date: "2026-12-01" }), "CONTRACT_SIGNED")).toBe(false);
    expect(needsContractDetails(deal(), "TOUR_COMPLETED")).toBe(false);
    expect(canMove(deal(), "LEAD_REGISTERED")).toBe(false);
    expect(canMove(deal(), "TOUR_COMPLETED")).toBe(true);
  });
  it("applies a move optimistically without touching other deals or mutating the old board", () => {
    const board: BoardData = { qualified: [], deals: [deal(), deal({ id: "d2" })] };
    const next = applyMove(board, "d1", "CONTRACT_SIGNED", { next_buyer_milestone_date: "2026-12-01" });
    expect(next.deals[0]).toMatchObject({ status: "CONTRACT_SIGNED", next_buyer_milestone_date: "2026-12-01" });
    expect(next.deals[1].status).toBe("LEAD_REGISTERED");
    expect(board.deals[0].status).toBe("LEAD_REGISTERED");
  });
  it("totals commission and the open pipeline", () => {
    const deals = [deal(), deal({ id: "2", status: "COMMISSION_PAID", expected_commission_etb: 100 }), deal({ id: "3", status: "CLOSED_LOST" }), deal({ id: "4", expected_commission_etb: null })];
    expect(expectedCommission(deals)).toBe(990_100); // a plain sum: 495,000 + 100 + 495,000 + nothing
    expect(expectedCommission(openPipeline(deals))).toBe(495_000);
    expect(openPipeline(deals).map((d) => d.id)).toEqual(["d1", "4"]);
  });
});

// ---------------- live events ----------------

let nextId = 1;
const base = (thread: string) => ({ id: nextId++, ts: "2026-11-02T10:00:00Z", thread_id: thread, buyer_id: "b-" + thread });
const started = (t: string): LiveEvent => ({ ...base(t), kind: "turn_started" });
const node = (t: string, name: string, ms = 10, extra: Partial<NodeEvent> = {}): LiveEvent => ({ ...base(t), kind: "node", node: name, ms, ...extra });
const finished = (t: string, ms: number): LiveEvent => ({ ...base(t), kind: "turn_finished", ms });

describe("live event folding", () => {
  it("groups nodes into turns per thread, interleaved, newest first", () => {
    const turns = foldTurns([started("a"), started("b"), node("a", "intake"), node("b", "intake"), node("a", "router"), finished("a", 900), node("b", "router")]);
    expect(turns.map((t) => t.thread_id)).toEqual(["b", "a"]);
    const a = turns.find((t) => t.thread_id === "a")!;
    expect(a.nodes.map((n) => n.node)).toEqual(["intake", "router"]);
    expect([a.status, a.totalMs]).toEqual(["done", 900]);
    expect(turns.find((t) => t.thread_id === "b")!.status).toBe("running");
  });
  it("records failures and starts a fresh turn each time", () => {
    const turns = foldTurns([started("a"), node("a", "intake"), { ...base("a"), kind: "turn_failed", error: "Boom" }, started("a"), node("a", "intake")]);
    expect(turns).toHaveLength(2);
    expect(turns[1]).toMatchObject({ status: "failed", error: "Boom" });
    expect(turns[0].status).toBe("running");
  });
  it("copes with joining mid-turn and ignores unrelated events", () => {
    const turns = foldTurns([node("z", "discovery"), { ...base("z"), kind: "takeover", buyer_id: "b", on: true, name: null }, finished("z", 5), finished("never-started", 5)]);
    expect(turns).toHaveLength(1);
    expect(turns[0]).toMatchObject({ thread_id: "z", status: "done" });
  });
  it("keeps only the most recent turns", () => {
    const events = Array.from({ length: 40 }, (_, i) => started(`t${i}`));
    expect(foldTurns(events, 30)).toHaveLength(30);
    expect(foldTurns(events, 30)[0].thread_id).toBe("t39");
  });
  it("sums repeated nodes such as discovery after a relaxation", () => {
    const scope = { relaxation_step: 1, max_relaxation_steps: 2, effective_max_budget_etb: 1, effective_sub_cities: [] };
    const [turn] = foldTurns([started("a"), node("a", "discovery", 100), node("a", "relaxer", 1, { search_scope: scope }), node("a", "discovery", 120)]);
    expect(nodeTimings(turn).get("discovery")).toEqual({ runs: 2, ms: 220 });
    expect(describeNode(turn.nodes[1])).toBe("widened to step 1");
  });
});
