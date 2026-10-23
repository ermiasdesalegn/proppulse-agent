import { fmtEtb } from "./format";
import type { LiveEvent, ModelCallEvent, NodeEvent, ToolEvent } from "./types";

/** The nodes of the agent graph, in the order the inspector lays them out. */
export const GRAPH_NODES = ["intake", "router", "discovery", "relaxer", "recommend", "waitlist", "analyst", "scheduler", "human_gate", "smalltalk"] as const;
export type GraphNode = (typeof GRAPH_NODES)[number];

export interface Turn {
  key: string;
  thread_id: string;
  buyer_id: string;
  startedAt: string;
  status: "running" | "done" | "failed";
  totalMs: number | null;
  error?: string;
  nodes: NodeEvent[];
  calls: ModelCallEvent[];
  tools: ToolEvent[];
}

/** Group the raw stream into turns, newest first. A node event with no open turn (the page joined mid-turn) opens one. */
export function foldTurns(events: LiveEvent[], max = 30): Turn[] {
  const open = new Map<string, Turn>();
  const turns: Turn[] = [];
  const start = (e: { id: number; ts: string; thread_id: string; buyer_id: string }): Turn => {
    const turn: Turn = { key: `${e.thread_id}#${e.id}`, thread_id: e.thread_id, buyer_id: e.buyer_id, startedAt: e.ts, status: "running", totalMs: null, nodes: [], calls: [], tools: [] };
    open.set(e.thread_id, turn);
    turns.push(turn);
    return turn;
  };
  // Model calls and tool calls name the buyer, not the thread: they belong to that buyer's turn that is open when they happen.
  const openFor = (buyerId: string | undefined | null) => [...open.values()].find((t) => t.buyer_id === buyerId);
  for (const e of events) {
    if (e.kind === "turn_started") start(e);
    else if (e.kind === "node") (open.get(e.thread_id) ?? start(e)).nodes.push(e);
    else if (e.kind === "model_call") openFor(e.buyer_id)?.calls.push(e);
    else if (e.kind === "tool") openFor(e.buyer_id)?.tools.push(e);
    else if (e.kind === "turn_finished" || e.kind === "turn_failed") {
      const turn = open.get(e.thread_id);
      if (!turn) continue;
      turn.status = e.kind === "turn_finished" ? "done" : "failed";
      if (e.kind === "turn_finished") turn.totalMs = e.ms; else turn.error = e.error;
      open.delete(e.thread_id);
    }
  }
  return turns.slice(-max).reverse();
}

/** Which nodes ran in a turn, and how long each took (a node that ran twice, like discovery, sums). */
export function nodeTimings(turn: Turn): Map<string, { runs: number; ms: number }> {
  const out = new Map<string, { runs: number; ms: number }>();
  for (const n of turn.nodes) {
    const prev = out.get(n.node) ?? { runs: 0, ms: 0 };
    out.set(n.node, { runs: prev.runs + 1, ms: prev.ms + n.ms });
  }
  return out;
}

export function describeNode(n: NodeEvent): string {
  switch (n.node) {
    case "intake": return n.transcript_failed ? "Could not understand the voice note" : "Read the message";
    case "router": return [n.intent && `intent: ${n.intent}`, n.preferences?.max_budget_etb && `budget ${fmtEtb(n.preferences.max_budget_etb)}`].filter(Boolean).join(", ") || "Extracted details";
    case "discovery": return n.matches ? `${n.matches.length} match${n.matches.length === 1 ? "" : "es"}` : "Searched";
    case "relaxer": return n.search_scope ? `widened to step ${n.search_scope.relaxation_step}` : "Widened the search";
    case "recommend": return "Wrote the options";
    case "waitlist": return "No match; buyer put on the watchlist";
    case "analyst": return n.compared ? `compared ${n.compared.length} units` : "Compared units";
    case "scheduler": return n.booked ? "Booked the tour" : "Asked for booking details";
    case "human_gate": return "Checked for hand-off";
    case "smalltalk": return "Chatted";
    default: return n.node;
  }
}

/** A node run as a bar on the timeline: when it started (ms from the start of the turn), how long it took, and what it called. */
export interface TimelineRow {
  node: string;
  startMs: number;
  ms: number;
  event: NodeEvent;
  calls: ModelCallEvent[];
  tools: ToolEvent[];
}

/** Give each call to a run of the node it names; when a node ran more than once (discovery), the calls are shared out in order. */
function distribute<T extends { node: string | null }>(items: T[], rows: TimelineRow[], assign: (row: TimelineRow, item: T) => void): void {
  const byNode = new Map<string, T[]>();
  for (const item of items) if (item.node) byNode.set(item.node, [...(byNode.get(item.node) ?? []), item]);
  for (const [node, list] of byNode) {
    const runs = rows.filter((r) => r.node === node);
    if (!runs.length) continue;
    list.forEach((item, i) => assign(runs[Math.min(runs.length - 1, Math.floor((i * runs.length) / list.length))], item));
  }
}

/** Lay the node runs end to end (each event reports the time since the previous one) and attach the model and tool calls made in them. */
export function buildTimeline(turn: Turn): { rows: TimelineRow[]; totalMs: number } {
  let cursor = 0;
  const rows: TimelineRow[] = turn.nodes.map((event) => {
    const row: TimelineRow = { node: event.node, startMs: cursor, ms: event.ms, event, calls: [], tools: [] };
    cursor += event.ms;
    return row;
  });
  distribute(turn.calls, rows, (row, call) => row.calls.push(call));
  distribute(turn.tools, rows, (row, tool) => row.tools.push(tool));
  return { rows, totalMs: Math.max(turn.totalMs ?? 0, cursor) };
}

/** Which models answered in a turn, how many tokens it used, and how many requests had to fall back or retry. */
export function turnModelSummary(turn: Turn): { models: string[]; calls: number; tokens: number; fallbacks: number; retries: number; errors: number } {
  const ok = turn.calls.filter((c) => c.outcome === "ok");
  return {
    models: [...new Set(ok.map((c) => c.model).filter((m): m is string => !!m))],
    calls: turn.calls.length,
    tokens: turn.calls.reduce((n, c) => n + (c.input_tokens ?? 0) + (c.output_tokens ?? 0), 0),
    fallbacks: turn.calls.filter((c) => c.fallbacks.length > 0).length,
    retries: turn.calls.filter((c) => c.attempt > 1).length,
    errors: turn.calls.filter((c) => c.outcome !== "ok").length,
  };
}

/** `13200000` becomes "13.2M", used for the compact argument chips. */
export function chip(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (typeof value === "number") return Math.abs(value) >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(2))}M` : String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(chip).join(", ");
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k} ${chip(v)}`).join(" · ");
  return String(value);
}
