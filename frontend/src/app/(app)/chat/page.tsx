"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "@/components/clsx";
import { Button, Card, ErrorNote, Pill } from "@/components/ui";
import { api, errorMessage, post } from "@/lib/api";
import { fmtEtb, fmtTime } from "@/lib/format";
import type { ChatMessage } from "@/lib/types";

const STORAGE_KEY = "pp_chat_session";

interface WebReply {
  buyer_id: string;
  reply: string | null;
  takeover: boolean;
  error: boolean;
  active_matches: { title: string; price_etb: number; sub_city: string; bedrooms: number; similarity: number }[];
}

interface Poll {
  buyer_id: string | null;
  takeover: boolean;
  messages: ChatMessage[];
}

function newSession(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 24);
}

const SUGGESTIONS = [
  "Hi! I'm looking for a 3 bedroom apartment in Bole, budget 25 million birr.",
  "Compare the first two options.",
  "I'd like to visit the first one tomorrow at 10am. I'm Abel Tesfaye, 0911000000.",
];

export default function ChatPage() {
  const [session, setSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [takeover, setTakeover] = useState(false);
  const [matches, setMatches] = useState<WebReply["active_matches"]>([]);
  const [text, setText] = useState("");
  const [waiting, setWaiting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The message you just sent, shown at once. The database can take a couple of seconds to confirm it.
  const [unconfirmed, setUnconfirmed] = useState<{ key: number; content: string; at: string }[]>([]);
  const lastId = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let id = localStorage.getItem(STORAGE_KEY);
    if (!id) { id = newSession(); localStorage.setItem(STORAGE_KEY, id); }
    setSession(id);
  }, []);

  const refresh = useCallback(async () => {
    if (!session) return;
    try {
      const poll = await api<Poll>(`chat/web/${session}/messages?after_id=${lastId.current}`);
      setTakeover(poll.takeover);
      if (poll.messages.length) {
        lastId.current = Math.max(lastId.current, ...poll.messages.map((m) => m.id));
        setMessages((prev) => [...prev, ...poll.messages.filter((m) => !prev.some((p) => p.id === m.id))]);
      }
    } catch (e) { setError(errorMessage(e)); }
  }, [session]);

  // Broker replies and proactive alerts arrive without the buyer asking, so keep listening.
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  // Hide an unconfirmed message once the stored copy of it has arrived.
  const confirmed = new Set(messages.filter((m) => m.role === "buyer").map((m) => m.content));
  const shownUnconfirmed = unconfirmed.filter((u) => !confirmed.has(u.content));

  // Scroll the conversation box itself; scrollIntoView would scroll the whole page as well.
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [messages.length, shownUnconfirmed.length, waiting]);

  useEffect(() => {
    if (waiting === null) return;
    const timer = setInterval(() => setWaiting((s) => (s === null ? null : s + 1)), 1000);
    return () => clearInterval(timer);
  }, [waiting === null]); // eslint-disable-line react-hooks/exhaustive-deps

  async function send(message: string) {
    if (!session || !message.trim() || waiting !== null) return;
    setError(null);
    setText("");
    setWaiting(0);
    setUnconfirmed((u) => [...u, { key: Date.now(), content: message, at: new Date().toISOString() }]);
    setTimeout(() => void refresh(), 1500);
    try {
      const reply = await post<WebReply>("chat/web", { session_id: session, text: message });
      setTakeover(reply.takeover);
      if (reply.active_matches.length) setMatches(reply.active_matches);
    } catch (e) {
      setError(errorMessage(e));
    }
    setWaiting(null);
    await refresh();
    setUnconfirmed([]); // by now the stored copies are in the list
  }

  function reset() {
    const id = newSession();
    localStorage.setItem(STORAGE_KEY, id);
    lastId.current = 0;
    setSession(id); setMessages([]); setMatches([]); setTakeover(false); setError(null);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Chat Simulator</h1>
          <p className="text-sm text-muted">Talk to the agent as a buyer, without Telegram. Open Agent Brain in another tab to watch it think.</p>
        </div>
        <Button onClick={reset}>New conversation</Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card className="flex h-[70vh] min-h-[28rem] flex-col">
          <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite" aria-label="Conversation">
            {messages.length === 0 && shownUnconfirmed.length === 0 && (
              <div className="py-8 text-center text-sm text-muted">
                <p>Say hello, or try one of these:</p>
                <div className="mt-3 flex flex-col items-center gap-2">
                  {SUGGESTIONS.map((s) => <button key={s} onClick={() => void send(s)} className="max-w-md rounded-full border border-line bg-raised px-3 py-1.5 text-left text-xs hover:border-accent">{s}</button>)}
                </div>
              </div>
            )}
            {messages.map((m) => m.source === "system" ? (
              <p key={m.id} className="text-center text-xs text-muted">{m.content}</p>
            ) : (
              <div key={m.id} className={clsx("flex", m.role === "buyer" ? "justify-end" : "justify-start")}>
                <div className={clsx("max-w-[85%] rounded-xl border px-3 py-2 text-sm", m.role === "buyer" ? "border-accent/30 bg-accent/10" : m.role === "agent" ? "border-line bg-raised" : "border-warn/40 bg-warn/10")}>
                  <p className="mb-1 text-xs text-muted"><span className="font-semibold">{m.role === "buyer" ? "You" : m.role === "agent" ? "Agent" : "Broker"}</span> <time>{fmtTime(m.created_at)}</time></p>
                  <p className="whitespace-pre-wrap break-words">{m.content}</p>
                </div>
              </div>
            ))}
            {shownUnconfirmed.map((u) => (
              <div key={u.key} className="flex justify-end">
                <div className="max-w-[85%] rounded-xl border border-accent/30 bg-accent/10 px-3 py-2 text-sm opacity-80">
                  <p className="mb-1 text-xs text-muted"><span className="font-semibold">You</span> <time>{fmtTime(u.at)}</time></p>
                  <p className="whitespace-pre-wrap break-words">{u.content}</p>
                </div>
              </div>
            ))}
            {waiting !== null && (
              <div className="flex justify-start">
                <div className="rounded-xl border border-line bg-raised px-3 py-2 text-sm text-muted" role="status">Agent is thinking... <span className="tnum">{waiting}s</span></div>
              </div>
            )}
          </div>

          <form onSubmit={(e) => { e.preventDefault(); void send(text); }} className="border-t border-line p-3">
            {error && <div className="mb-2"><ErrorNote>{error}</ErrorNote></div>}
            {takeover && <p className="mb-2 text-xs text-warn">The broker has taken over this chat, so the agent will not reply automatically.</p>}
            <div className="flex gap-2">
              <label htmlFor="chat-input" className="sr-only">Message</label>
              <input id="chat-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} autoComplete="off" placeholder="Type a message as the buyer"
                className="flex-1 rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent" />
              <Button type="submit" variant="primary" disabled={waiting !== null || !text.trim()}>Send</Button>
            </div>
          </form>
        </Card>

        <Card title="Options shown" action={<Pill>{matches.length}</Pill>}>
          {matches.length === 0 ? <p className="px-4 py-6 text-sm text-muted">Properties the agent recommends appear here, numbered as the agent numbers them.</p> : (
            <ol className="divide-y divide-line/60">
              {matches.map((m, i) => (
                <li key={m.title} className="px-4 py-3 text-sm">
                  <p className="font-medium"><span className="tnum mr-1 text-accent">{i + 1}.</span>{m.title}</p>
                  <p className="text-xs text-muted">{m.sub_city} - {m.bedrooms} BR - {fmtEtb(m.price_etb)} - match {(m.similarity * 100).toFixed(0)}%</p>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}
