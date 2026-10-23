"use client";

import { useMemo, useState } from "react";
import { useBuyerNames } from "@/components/Activity";
import clsx from "@/components/clsx";
import { Card, Empty, Pill } from "@/components/ui";
import { GRAPH_NODES, describeNode, foldTurns, nodeTimings, type Turn } from "@/lib/events";
import { fmtDuration, fmtEtb, fmtTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { NodeEvent } from "@/lib/types";

const W = 120, H = 40;
const POS: Record<string, { x: number; y: number }> = {
  intake: { x: 10, y: 170 }, router: { x: 170, y: 170 },
  discovery: { x: 350, y: 50 }, analyst: { x: 350, y: 150 }, scheduler: { x: 350, y: 250 }, smalltalk: { x: 350, y: 330 },
  relaxer: { x: 550, y: 0 }, recommend: { x: 550, y: 75 }, waitlist: { x: 550, y: 135 }, human_gate: { x: 550, y: 250 },
};
const LABEL: Record<string, string> = { human_gate: "hand-off check" };
const EDGES: [string, string][] = [
  ["intake", "router"], ["router", "discovery"], ["router", "analyst"], ["router", "scheduler"], ["router", "smalltalk"],
  ["discovery", "relaxer"], ["relaxer", "discovery"], ["discovery", "recommend"], ["discovery", "waitlist"], ["scheduler", "human_gate"],
];

const out = (n: string) => ({ x: POS[n].x + W, y: POS[n].y + H / 2 });
const into = (n: string) => ({ x: POS[n].x, y: POS[n].y + H / 2 });

function Graph({ turn, selected, onSelect }: { turn: Turn | undefined; selected: string | null; onSelect: (node: string) => void }) {
  const timings = turn ? nodeTimings(turn) : new Map<string, { runs: number; ms: number }>();
  const last = turn && turn.status === "running" ? turn.nodes[turn.nodes.length - 1]?.node : null;
  const ran = (n: string) => timings.has(n);

  return (
    <svg viewBox="0 0 690 380" className="w-full" role="img" aria-label="The agent's processing steps and which ones ran in the selected turn">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor" /></marker>
      </defs>
      {EDGES.map(([a, b]) => {
        const from = out(a), to = into(b);
        const on = ran(a) && ran(b);
        const loop = a === "relaxer" && b === "discovery";
        const d = loop ? `M ${from.x - W} ${from.y + 8} C ${from.x - 90} ${from.y + 40}, ${to.x + 150} ${to.y - 30}, ${to.x + W - 10} ${to.y - H / 2 + 2}` : `M ${from.x} ${from.y} L ${to.x - 4} ${to.y}`;
        return <path key={`${a}-${b}`} d={d} fill="none" strokeWidth={on ? 2 : 1.25} markerEnd="url(#arrow)" className={on ? "text-accent" : "text-line"} stroke="currentColor" />;
      })}
      {GRAPH_NODES.map((n) => {
        const { x, y } = POS[n];
        const t = timings.get(n);
        const isSel = selected === n;
        return (
          <g key={n} transform={`translate(${x},${y})`} onClick={() => onSelect(n)} className="cursor-pointer" role="button" tabIndex={0} aria-label={`${LABEL[n] ?? n}${t ? `, ran ${t.runs} time${t.runs > 1 ? "s" : ""}, ${fmtDuration(t.ms)}` : ", did not run"}`}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n); } }}>
            {last === n && <rect width={W} height={H} rx={9} fill="none" stroke="rgb(var(--accent))" strokeWidth={2} className="pulse-ring" />}
            <rect width={W} height={H} rx={9} strokeWidth={isSel ? 2.5 : 1.5}
              fill={t ? "rgb(var(--accent) / 0.12)" : "rgb(var(--panel))"} stroke={t || isSel ? "rgb(var(--accent))" : "rgb(var(--line))"} />
            <text x={W / 2} y={t ? 17 : 24} textAnchor="middle" fontSize="12.5" fontWeight="600" fill={t ? "rgb(var(--ink))" : "rgb(var(--muted))"}>{LABEL[n] ?? n}</text>
            {t && <text x={W / 2} y={32} textAnchor="middle" fontSize="11" className="tnum" fill="rgb(var(--muted))">{t.runs > 1 ? `${t.runs}x - ` : ""}{fmtDuration(t.ms)}</text>}
          </g>
        );
      })}
    </svg>
  );
}

function Detail({ events }: { events: NodeEvent[] }) {
  return (
    <ul className="space-y-3">
      {events.map((e) => (
        <li key={e.id} className="rounded-lg border border-line bg-raised p-3 text-sm">
          <p className="flex items-center justify-between"><span className="font-medium">{describeNode(e)}</span><span className="tnum text-xs text-muted">{fmtDuration(e.ms)}</span></p>
          {e.preferences && (
            <p className="mt-2 text-xs text-muted">Understood: budget {fmtEtb(e.preferences.max_budget_etb)}, {e.preferences.min_bedrooms ?? "any"} bedrooms, areas {e.preferences.preferred_sub_cities.join(", ") || "any"}{e.urgency ? `, urgency ${e.urgency}` : ""}</p>
          )}
          {e.query_text && <p className="mt-1 text-xs text-muted">Search text: &ldquo;{e.query_text}&rdquo;</p>}
          {e.search_scope && <p className="mt-1 text-xs text-muted">Search scope: step {e.search_scope.relaxation_step} of {e.search_scope.max_relaxation_steps}, up to {fmtEtb(e.search_scope.effective_max_budget_etb)}, areas {e.search_scope.effective_sub_cities.join(", ") || "any"}</p>}
          {e.matches && (
            <ol className="mt-2 space-y-1 text-xs">
              {e.matches.length === 0 && <li className="text-muted">No units cleared the similarity threshold.</li>}
              {e.matches.map((m, i) => <li key={m.title}><span className="tnum text-accent">{i + 1}.</span> {m.title} <span className="text-muted">- {fmtEtb(m.price_etb)}, match {(m.similarity * 100).toFixed(0)}%</span></li>)}
            </ol>
          )}
          {e.reply && <p className="mt-2 break-words border-l-2 border-accent/40 pl-2 text-xs text-muted">{e.reply}</p>}
        </li>
      ))}
    </ul>
  );
}

export default function BrainPage() {
  const { events, status } = useLive();
  const nameOf = useBuyerNames();
  const turns = useMemo(() => foldTurns(events), [events]);
  const [turnKey, setTurnKey] = useState<string | null>(null);
  const [node, setNode] = useState<string | null>(null);

  const turn = turns.find((t) => t.key === turnKey) ?? turns[0];
  const detail = turn?.nodes.filter((n) => n.node === node) ?? [];
  const timings = turn ? nodeTimings(turn) : new Map<string, { runs: number; ms: number }>();
  const slowest = Math.max(1, ...[...timings.values()].map((t) => t.ms));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Agent Brain</h1>
          <p className="text-sm text-muted">Every step the agent takes while a buyer chats, live. Click a step to see what it decided.</p>
        </div>
        <Pill tone={status === "live" ? "good" : "warn"}>{status === "live" ? "Streaming" : status === "connecting" ? "Connecting" : "Reconnecting"}</Pill>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <Card title={turn ? `${nameOf(turn.buyer_id)} - ${fmtTime(turn.startedAt)}` : "No turns yet"} action={turn && <Pill tone={turn.status === "done" ? "good" : turn.status === "failed" ? "bad" : "accent"}>{turn.status === "done" ? `Done in ${fmtDuration(turn.totalMs ?? 0)}` : turn.status === "failed" ? `Failed (${turn.error})` : "Running"}</Pill>}>
            {turn ? <div className="p-3"><Graph turn={turn} selected={node} onSelect={setNode} /></div> : <Empty>Send a message with the Chat Simulator or on Telegram and watch the steps light up here.</Empty>}
          </Card>

          {turn && (
            <Card title="Time per step">
              <ul className="space-y-2 p-4">
                {[...timings.entries()].map(([name, t]) => (
                  <li key={name} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-3 text-sm">
                    <span className="font-mono text-xs">{name}</span>
                    <div className="h-2 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(2, (t.ms / slowest) * 100)}%` }} /></div>
                    <span className="tnum text-right text-xs text-muted">{fmtDuration(t.ms)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {node && (
            <Card title={`What "${LABEL[node] ?? node}" did`}>
              <div className="p-4">{detail.length ? <Detail events={detail} /> : <p className="text-sm text-muted">This step did not run in the selected turn.</p>}</div>
            </Card>
          )}
        </div>

        <Card title="Recent turns">
          {turns.length === 0 ? <Empty>Nothing yet.</Empty> : (
            <ul className="max-h-[40rem] overflow-y-auto">
              {turns.map((t) => (
                <li key={t.key}>
                  <button onClick={() => { setTurnKey(t.key); setNode(null); }} aria-pressed={t.key === turn?.key}
                    className={clsx("w-full border-b border-line/60 px-4 py-3 text-left hover:bg-raised", t.key === turn?.key && "bg-accent/10")}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate font-medium">{nameOf(t.buyer_id)}</span>
                      <span className="tnum text-xs text-muted">{fmtTime(t.startedAt)}</span>
                    </div>
                    <p className="mt-1 break-words font-mono text-xs text-muted">{t.nodes.map((n) => n.node).join(" > ") || "starting..."}</p>
                    <p className="mt-1 text-xs">{t.status === "done" ? <span className="text-good">{fmtDuration(t.totalMs ?? 0)}</span> : t.status === "failed" ? <span className="text-bad">failed</span> : <span className="text-accent">running</span>}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
