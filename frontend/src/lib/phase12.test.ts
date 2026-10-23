import { describe, expect, it } from "vitest";
import { STAGE_LABEL, fallbackRate, messageSeries, scoreBandLabel, scoreBands } from "./analytics";
import { buildTimeline, chip, foldTurns, turnModelSummary } from "./events";
import type { LiveEvent, ModelCallEvent, NodeEvent, ToolEvent } from "./types";

let id = 0;
const ts = "2026-11-02T08:00:00Z";
const started = (thread: string, buyer: string): LiveEvent => ({ id: ++id, ts, kind: "turn_started", thread_id: thread, buyer_id: buyer });
const finished = (thread: string, buyer: string, ms: number): LiveEvent => ({ id: ++id, ts, kind: "turn_finished", thread_id: thread, buyer_id: buyer, ms });
const node = (thread: string, buyer: string, name: string, ms: number): NodeEvent => ({ id: ++id, ts, kind: "node", thread_id: thread, buyer_id: buyer, node: name, ms });
const call = (buyer: string, nodeName: string | null, over: Partial<ModelCallEvent> = {}): ModelCallEvent => ({
  id: ++id, ts, kind: "model_call", operation: "generate_structured", schema_name: "Extraction", lane: "generate", model: "model-a", outcome: "ok",
  latency_ms: 900, attempt: 1, fallbacks: [], input_tokens: 400, output_tokens: 100, node: nameOrNull(nodeName), buyer_id: buyer, ...over,
});
const nameOrNull = (n: string | null) => n;
const tool = (buyer: string, nodeName: string | null, name: string, over: Partial<ToolEvent> = {}): ToolEvent => ({
  id: ++id, ts, kind: "tool", tool: name, args: {}, result: null, ms: 12, ok: true, error: null, node: nodeName, buyer_id: buyer, ...over,
});

describe("turns with model and tool calls", () => {
  it("attaches calls to the open turn of the buyer they name, not to another buyer's", () => {
    const [a, b] = [["t-a", "buyer-a"], ["t-b", "buyer-b"]];
    const events = [started(a[0], a[1]), started(b[0], b[1]), call(a[1], "router"), call(b[1], "router", { model: "model-b" }), tool(a[1], "discovery", "search.search_properties"),
      node(a[0], a[1], "router", 1000), node(b[0], b[1], "router", 500), finished(a[0], a[1], 1200), finished(b[0], b[1], 800)];
    const turns = foldTurns(events);
    const turnA = turns.find((t) => t.buyer_id === "buyer-a")!;
    const turnB = turns.find((t) => t.buyer_id === "buyer-b")!;
    expect(turnA.calls.map((c) => c.model)).toEqual(["model-a"]);
    expect(turnB.calls.map((c) => c.model)).toEqual(["model-b"]);
    expect(turnA.tools.map((t) => t.tool)).toEqual(["search.search_properties"]);
    expect(turnB.tools).toEqual([]);
  });

  it("ignores calls that arrive when the buyer has no open turn (a background job, a late event)", () => {
    const turns = foldTurns([call("nobody", "router"), started("t", "b"), finished("t", "b", 10), call("b", "router")]);
    expect(turns).toHaveLength(1);
    expect(turns[0].calls).toEqual([]);
  });
});

describe("timeline", () => {
  const build = (events: LiveEvent[]) => buildTimeline(foldTurns(events)[0]);

  it("lays the steps end to end and reports the total", () => {
    const { rows, totalMs } = build([started("t", "b"), node("t", "b", "intake", 5), node("t", "b", "router", 1000), node("t", "b", "discovery", 400), finished("t", "b", 1500)]);
    expect(rows.map((r) => [r.node, r.startMs, r.ms])).toEqual([["intake", 0, 5], ["router", 5, 1000], ["discovery", 1005, 400]]);
    expect(totalMs).toBe(1500);
  });

  it("falls back to the sum of the steps while the turn is still running", () => {
    expect(build([started("t", "b"), node("t", "b", "router", 700)]).totalMs).toBe(700);
  });

  it("gives each call to the step it names", () => {
    const { rows } = build([started("t", "b"), call("b", "router"), tool("b", "scheduler", "calendar.book"), node("t", "b", "router", 900), node("t", "b", "scheduler", 300), finished("t", "b", 1200)]);
    expect(rows[0].calls).toHaveLength(1);
    expect(rows[1].tools.map((t) => t.tool)).toEqual(["calendar.book"]);
    expect(rows[0].tools).toEqual([]);
  });

  it("shares calls out in order when a step ran twice, as discovery does after the search is widened", () => {
    const events = [started("t", "b"), tool("b", "discovery", "search.search_properties", { result: { count: 0 } }), tool("b", "discovery", "search.search_properties", { result: { count: 2 } }),
      node("t", "b", "discovery", 100), node("t", "b", "relaxer", 1), node("t", "b", "discovery", 120), finished("t", "b", 300)];
    const { rows } = build(events);
    const discoveries = rows.filter((r) => r.node === "discovery");
    expect(discoveries.map((r) => r.tools.length)).toEqual([1, 1]);
    expect((discoveries[0].tools[0].result as { count: number }).count).toBe(0);
    expect((discoveries[1].tools[0].result as { count: number }).count).toBe(2);
  });

  it("leaves out a call that names no step in the turn rather than inventing a place for it", () => {
    const { rows } = build([started("t", "b"), call("b", "scheduler"), node("t", "b", "router", 10), finished("t", "b", 10)]);
    expect(rows.flatMap((r) => r.calls)).toEqual([]);
  });
});

describe("model summary", () => {
  it("counts models that answered, tokens, fall-backs, retries and failures", () => {
    const turn = foldTurns([started("t", "b"), call("b", "router", { fallbacks: ["model-big"], model: "model-a" }), call("b", "router", { outcome: "error:ServerError", model: null, attempt: 1, input_tokens: null, output_tokens: null }),
      call("b", "recommend", { attempt: 2, model: "model-b", input_tokens: 10, output_tokens: 5 }), finished("t", "b", 100)])[0];
    expect(turnModelSummary(turn)).toEqual({ models: ["model-a", "model-b"], calls: 3, tokens: 515, fallbacks: 1, retries: 1, errors: 1 });
  });

  it("is empty for a turn that made no model requests", () => {
    expect(turnModelSummary(foldTurns([started("t", "b"), finished("t", "b", 5)])[0])).toEqual({ models: [], calls: 0, tokens: 0, fallbacks: 0, retries: 0, errors: 0 });
  });
});

describe("argument chips", () => {
  it("shows money compactly and lists plainly", () => {
    expect(chip(13_200_000)).toBe("13.2M");
    expect(chip(275_000)).toBe("275000");
    expect(chip(["Bole", "CMC"])).toBe("Bole, CMC");
    expect(chip(null)).toBe("none");
    expect(chip({ count: 2, titles: ["A", "B"] })).toBe("count 2 · titles A, B");
    expect(chip("a***@b.test")).toBe("a***@b.test");
  });
});

describe("analytics helpers", () => {
  const base = { generated_at: "2026-11-04T10:00:00Z", days: 7 };

  it("fills quiet days with zeros so gaps are visible", () => {
    const series = messageSeries({ ...base, messages_per_day: [{ day: "2026-11-02", role: "buyer", n: 3 }, { day: "2026-11-02", role: "agent", n: 3 }, { day: "2026-11-04", role: "buyer", n: 1 }] });
    expect(series).toHaveLength(7);
    expect(series.map((p) => p.value)).toEqual([0, 0, 0, 0, 6, 0, 1]);
    expect(series[4].detail).toBe("3 from buyers, 3 from the agent");
    expect(series[6].label).toBe("4 Nov");
  });

  it("groups a long window by week", () => {
    const series = messageSeries({ generated_at: "2026-11-30T00:00:00Z", days: 90, messages_per_day: [{ day: "2026-11-30", role: "agent", n: 4 }] });
    expect(series.length).toBe(13);
    expect(series[series.length - 1].value).toBe(4);
  });

  it("always shows the five score bands", () => {
    const bands = scoreBands([{ from: 40, to: 59, buyers: 2 }, { from: 80, to: 100, buyers: 1 }]);
    expect(bands.map((b) => [scoreBandLabel(b), b.buyers])).toEqual([["0-19", 0], ["20-39", 0], ["40-59", 2], ["60-79", 0], ["80-100", 1]]);
  });

  it("reports how often a model request fell back, and zero when there were none", () => {
    expect(fallbackRate([])).toBe(0);
    expect(fallbackRate([{ model: "a", calls: 6, avg_ms: 1, input_tokens: 0, output_tokens: 0, errors: 0, fell_back: 3 }, { model: "b", calls: 2, avg_ms: 1, input_tokens: 0, output_tokens: 0, errors: 0, fell_back: 0 }])).toBe(38);
  });

  it("has a readable name for every pipeline stage", () => {
    for (const stage of ["NEW", "QUALIFIED", "SITE_VISIT_BOOKED", "CONTRACT_SIGNED", "COMMISSION_PAID", "LEAD_REGISTERED", "CLOSED_LOST"]) expect(STAGE_LABEL[stage]).toBeTruthy();
  });
});
