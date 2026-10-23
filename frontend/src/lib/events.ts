import { fmtEtb } from "./format";
import type { LiveEvent, NodeEvent } from "./types";

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
}

/** Group the raw stream into turns, newest first. A node event with no open turn (the page joined mid-turn) opens one. */
export function foldTurns(events: LiveEvent[], max = 30): Turn[] {
  const open = new Map<string, Turn>();
  const turns: Turn[] = [];
  const start = (e: { id: number; ts: string; thread_id: string; buyer_id: string }): Turn => {
    const turn: Turn = { key: `${e.thread_id}#${e.id}`, thread_id: e.thread_id, buyer_id: e.buyer_id, startedAt: e.ts, status: "running", totalMs: null, nodes: [] };
    open.set(e.thread_id, turn);
    turns.push(turn);
    return turn;
  };
  for (const e of events) {
    if (e.kind === "turn_started") start(e);
    else if (e.kind === "node") (open.get(e.thread_id) ?? start(e)).nodes.push(e);
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
