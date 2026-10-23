"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { LiveEvent } from "./types";

export type LiveStatus = "connecting" | "live" | "reconnecting";

interface LiveValue {
  events: LiveEvent[];
  status: LiveStatus;
  /** Bumps whenever an event arrives that changes what lists show (messages, takeovers, proactive sends). */
  dataVersion: number;
}

const LiveContext = createContext<LiveValue>({ events: [], status: "connecting", dataVersion: 0 });

const KINDS = ["turn_started", "node", "turn_finished", "turn_failed", "message", "takeover", "proactive", "model_call", "tool", "demo_reset"] as const;
const DATA_KINDS = new Set(["message", "takeover", "proactive", "turn_finished", "demo_reset"]);
const KEEP = 800; // a turn is now about fifteen events (steps, model requests, tool calls)

/** One shared event stream for the whole dashboard. The browser reconnects by itself and resumes from the last id it saw. */
export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [dataVersion, setDataVersion] = useState(0);
  const lastId = useRef(0);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const open = () => {
      source = new EventSource("/api/proxy/stream/events");
      source.onopen = () => setStatus("live");
      source.onerror = () => {
        setStatus("reconnecting");
        if (source?.readyState === EventSource.CLOSED && !stopped) {
          // The browser gave up (for example the server restarted): start a new connection ourselves.
          source.close();
          retry = setTimeout(open, 3000);
        }
      };
      const onEvent = (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as LiveEvent;
        if (event.id <= lastId.current) return; // a replay after reconnecting can repeat what we already have
        lastId.current = event.id;
        setEvents((previous) => [...previous.slice(-(KEEP - 1)), event]);
        if (DATA_KINDS.has(event.kind)) setDataVersion((v) => v + 1);
      };
      for (const kind of KINDS) source.addEventListener(kind, onEvent as EventListener);
    };

    open();
    return () => {
      stopped = true;
      clearTimeout(retry);
      source?.close();
    };
  }, []);

  const value = useMemo(() => ({ events, status, dataVersion }), [events, status, dataVersion]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveValue {
  return useContext(LiveContext);
}
