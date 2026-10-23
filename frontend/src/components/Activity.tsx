"use client";

import { useEffect } from "react";
import { describeNode } from "@/lib/events";
import { displayName, fmtDuration, fmtTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { Buyer, LiveEvent } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import clsx from "./clsx";
import { Pill } from "./ui";

/** buyer id -> display name, refreshed now and then, for labelling live events. */
export function useBuyerNames(): (id: string | null) => string {
  const { dataVersion } = useLive();
  const { data, reload } = useApi<Buyer[]>("buyers", 20_000);
  // A brand-new buyer appears in the live stream before the list knows them, so refresh when messages arrive.
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) void reload() }, [dataVersion]);
  const names = new Map((data ?? []).map((b) => [b.id, displayName(b)]));
  return (id) => (id ? names.get(id) ?? "a buyer" : "the broker");
}

const ROLE_TONE = { buyer: "neutral", agent: "accent", broker: "warn" } as const;

export function ActivityRow({ event, nameOf }: { event: LiveEvent; nameOf: (id: string | null) => string }) {
  let body: React.ReactNode;
  switch (event.kind) {
    case "turn_started":
      body = <span className="text-muted">Turn started for {nameOf(event.buyer_id)}{event.inbound_kind === "voice" ? " (voice note)" : ""}</span>;
      break;
    case "node":
      body = (
        <span>
          <span className="font-mono text-xs font-semibold text-accent">{event.node}</span>{" "}
          <span className="text-muted">{fmtDuration(event.ms)} - {describeNode(event)}</span>
        </span>
      );
      break;
    case "turn_finished":
      body = <span className="text-good">Turn finished in {fmtDuration(event.ms)}</span>;
      break;
    case "turn_failed":
      body = <span className="text-bad">Turn failed ({event.error})</span>;
      break;
    case "message":
      body = (
        <span>
          <Pill tone={ROLE_TONE[event.role]}>{event.role}{event.source === "voice" ? " voice" : ""}</Pill>{" "}
          <span className="text-muted">{nameOf(event.buyer_id)}:</span> {event.text}
        </span>
      );
      break;
    case "takeover":
      body = <span className="text-warn">Broker {event.on ? "took over" : "handed back"} the chat with {event.name ?? nameOf(event.buyer_id)}</span>;
      break;
    case "proactive":
      body = (
        <span>
          <Pill tone="good">{event.what.toLowerCase().replace(/_/g, " ")}</Pill>{" "}
          <span className="text-muted">to {event.audience === "broker" ? "you" : nameOf(event.buyer_id)} via {event.channel}:</span> {event.text}
        </span>
      );
      break;
  }
  return (
    <li className="flex gap-3 border-b border-line/60 px-4 py-2 text-sm last:border-b-0">
      <time className="tnum w-16 shrink-0 pt-0.5 text-xs text-muted">{fmtTime(event.ts)}</time>
      <div className="min-w-0 break-words">{body}</div>
    </li>
  );
}

export function ActivityList({ events, nameOf, limit = 40, className }: { events: LiveEvent[]; nameOf: (id: string | null) => string; limit?: number; className?: string }) {
  const shown = events.slice(-limit).reverse();
  if (shown.length === 0) return <p className="px-4 py-8 text-center text-sm text-muted">Waiting for activity. Message the bot, or use the Chat Simulator, and it appears here live.</p>;
  return <ul className={clsx("max-h-[28rem] overflow-y-auto", className)}>{shown.map((e) => <ActivityRow key={e.id} event={e} nameOf={nameOf} />)}</ul>;
}
