"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "@/components/clsx";
import ScorePanel from "@/components/ScorePanel";
import { Button, Card, Empty, ErrorNote, Pill, ScoreBar } from "@/components/ui";
import { errorMessage, post } from "@/lib/api";
import { displayName, fmtDateTime, fmtEtb, stageLabel, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { Buyer, ChatMessage } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STAGE_TONE: Record<string, "neutral" | "accent" | "good" | "warn" | "bad"> = {
  NEW: "neutral", QUALIFIED: "accent", INVENTORY_PENDING: "warn", SITE_VISIT_BOOKED: "accent", TOUR_COMPLETED: "accent",
  CONTRACT_SIGNED: "good", COMMISSION_PENDING: "good", COMMISSION_PAID: "good", LOST: "bad",
};

export default function CrmPage() {
  const { dataVersion } = useLive();
  const buyers = useApi<Buyer[]>("buyers", 15_000);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) void buyers.reload() }, [dataVersion]);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (buyers.data ?? []).filter((b) => !q || [displayName(b), b.phone ?? "", b.email ?? "", b.deal_stage].join(" ").toLowerCase().includes(q));
  }, [buyers.data, query]);

  const selected = (buyers.data ?? []).find((b) => b.id === selectedId) ?? list[0] ?? null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">CRM & Takeover</h1>
        <p className="text-sm text-muted">Every buyer the agent has talked to. Take over a chat to reply yourself.</p>
      </div>
      {buyers.error && <ErrorNote>{buyers.error}</ErrorNote>}

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <Card className="overflow-hidden">
          <div className="border-b border-line p-3">
            <label htmlFor="buyer-search" className="sr-only">Search buyers</label>
            <input id="buyer-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, phone, stage"
              className="w-full rounded-md border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent" />
          </div>
          {buyers.loading ? <Empty>Loading...</Empty> : list.length === 0 ? <Empty>No buyers match.</Empty> : (
            <ul className="max-h-[70vh] overflow-y-auto">
              {list.map((b) => (
                <li key={b.id}>
                  <button onClick={() => setSelectedId(b.id)} aria-pressed={selected?.id === b.id}
                    className={clsx("w-full border-b border-line/60 px-4 py-3 text-left hover:bg-raised", selected?.id === b.id && "bg-accent/10")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{displayName(b)}</span>
                      {b.human_takeover && <Pill tone="warn">You</Pill>}
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <Pill tone={STAGE_TONE[b.deal_stage] ?? "neutral"}>{stageLabel(b.deal_stage)}</Pill>
                      <ScoreBar score={b.lead_score} />
                    </div>
                    <p className="mt-1 text-xs text-muted">Updated {timeAgo(b.updated_at)}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {selected ? <BuyerPanel key={selected.id} buyer={selected} onChanged={() => void buyers.reload()} /> : <Card><Empty>Select a buyer to see the conversation.</Empty></Card>}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-sm">{value || <span className="text-muted">-</span>}</dd>
    </div>
  );
}

function BuyerPanel({ buyer, onChanged }: { buyer: Buyer; onChanged: () => void }) {
  const { dataVersion } = useLive();
  const messages = useApi<ChatMessage[]>(`buyers/${buyer.id}/messages`, 8_000);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) void messages.reload() }, [dataVersion]);
  // Scroll the transcript box itself; scrollIntoView would scroll the whole page as well.
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [messages.data?.length]);

  async function toggleTakeover() {
    setBusy(true); setError(null);
    try { await post(`buyers/${buyer.id}/takeover`, { on: !buyer.human_takeover }); onChanged(); await messages.reload(); }
    catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true); setError(null);
    try { await post("chat/broker-reply", { buyer_id: buyer.id, text }); setText(""); await messages.reload(); }
    catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  const channel = buyer.telegram_id ? "Telegram" : "Web chat";
  return (
    <Card className="flex min-h-[32rem] flex-col">
      <header className="border-b border-line px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">{displayName(buyer)}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Pill tone={STAGE_TONE[buyer.deal_stage] ?? "neutral"}>{stageLabel(buyer.deal_stage)}</Pill>
              <Pill>{channel}</Pill>
              <ScoreBar score={buyer.lead_score} />
            </div>
          </div>
          <Button variant={buyer.human_takeover ? "default" : "primary"} disabled={busy} onClick={toggleTakeover}>
            {buyer.human_takeover ? "Hand back to the agent" : "Take over chat"}
          </Button>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Budget" value={buyer.max_budget_etb ? fmtEtb(Number(buyer.max_budget_etb)) : null} />
          <Fact label="Bedrooms" value={buyer.min_bedrooms !== null ? `${buyer.min_bedrooms}+` : null} />
          <Fact label="Areas" value={buyer.preferred_sub_cities.join(", ")} />
          <Fact label="Phone" value={buyer.phone} />
          <Fact label="Email" value={buyer.email} />
          <div className="col-span-2 sm:col-span-3"><Fact label="What the agent understood" value={buyer.preference_summary} /></div>
        </dl>
      </header>

      <details className="border-b border-line">
        <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium hover:bg-raised">Lead score, worries and bookings</summary>
        <ScorePanel buyerId={buyer.id} />
      </details>

      <div ref={scroller} className="max-h-[32rem] min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-label="Conversation">
        {messages.error && <ErrorNote>{messages.error}</ErrorNote>}
        {messages.loading ? <Empty>Loading the conversation...</Empty> : (messages.data ?? []).length === 0 ? <Empty>No messages yet.</Empty> : null}
        {(messages.data ?? []).map((m) => <Bubble key={m.id} message={m} />)}
      </div>

      <form onSubmit={send} className="border-t border-line p-3">
        {error && <div className="mb-2"><ErrorNote>{error}</ErrorNote></div>}
        {!buyer.human_takeover && <p className="mb-2 text-xs text-muted">The agent is replying automatically. Take over the chat to write to {displayName(buyer)} yourself.</p>}
        <div className="flex gap-2">
          <label htmlFor="broker-reply" className="sr-only">Reply as the broker</label>
          <textarea id="broker-reply" value={text} onChange={(e) => setText(e.target.value)} disabled={!buyer.human_takeover || busy} rows={2} maxLength={4000}
            placeholder={buyer.human_takeover ? `Reply to ${displayName(buyer)} on ${channel}...` : "Take over to reply"}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void send(e as unknown as React.FormEvent); }}
            className="min-h-[2.75rem] flex-1 resize-y rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-50" />
          <Button variant="primary" type="submit" disabled={!buyer.human_takeover || busy || !text.trim()}>Send</Button>
        </div>
      </form>
    </Card>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  if (message.source === "system") {
    return <p className="text-center text-xs text-muted">{message.content} <span className="opacity-70">({fmtDateTime(message.created_at)})</span></p>;
  }
  const mine = message.role !== "buyer";
  return (
    <div className={clsx("flex", mine ? "justify-end" : "justify-start")}>
      <div className={clsx("max-w-[85%] rounded-xl border px-3 py-2 text-sm", message.role === "buyer" ? "border-line bg-raised" : message.role === "agent" ? "border-accent/30 bg-accent/10" : "border-warn/40 bg-warn/10")}>
        <p className="mb-1 flex items-center gap-2 text-xs text-muted">
          <span className="font-semibold">{message.role === "buyer" ? "Buyer" : message.role === "agent" ? "Agent" : "You"}</span>
          {message.source === "voice" && <Pill>Voice note</Pill>}
          <time>{fmtDateTime(message.created_at)}</time>
        </p>
        {message.source === "voice" && message.telegram_file_id && (
          <audio controls preload="none" className="mb-2 h-9 w-full" src={`/api/proxy/messages/${message.id}/audio`} />
        )}
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
    </div>
  );
}
