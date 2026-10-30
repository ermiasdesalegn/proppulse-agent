"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { buttonClass } from "@/components/site/ui";
import { chatFiles, clock, mergeMessages, newestId, STARTERS, stillWaiting, type ChatFile, type ChatReply, type MatchRef, type Polled, type QuickReply, type Unconfirmed, type WebMessage } from "@/lib/chat";
import { fmtEtb } from "@/lib/format";

type Action = "ask" | "cmp" | "book";
const ACTIONS: { action: Action; label: string; said: string }[] = [
  { action: "ask", label: "Tell me about it", said: "Tell me about this home" },
  { action: "cmp", label: "Compare with similar", said: "Compare this home with similar ones" },
  { action: "book", label: "Book a visit", said: "I would like to visit this home" },
];

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "content-type": "application/json" }, cache: "no-store" });
  if (res.status === 401) {
    window.location.href = "/login?next=/portal/chat";
    throw new Error("Please sign in");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "Something went wrong. Please try again.");
  return body as T;
}

export default function ChatClient({ about }: { about: { id: string; title: string } | null }) {
  const router = useRouter();
  const [messages, setMessages] = useState<WebMessage[]>([]);
  const [takeover, setTakeover] = useState(false);
  const [matches, setMatches] = useState<MatchRef[]>([]);
  const [quickReplies, setQuickReplies] = useState<QuickReply[][]>([]);
  const [text, setText] = useState("");
  const [waiting, setWaiting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Unconfirmed[]>([]);
  const [context, setContext] = useState(about);
  const [loaded, setLoaded] = useState(false);
  const lastId = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const poll = await call<Polled>(`/api/me/chat/messages?after_id=${lastId.current}`);
      setTakeover(poll.takeover);
      if (poll.messages.length) {
        lastId.current = Math.max(lastId.current, newestId(poll.messages));
        setMessages((shown) => mergeMessages(shown, poll.messages));
      }
      setLoaded(true);
    } catch {
      setLoaded(true); // the next check tries again; a send shows its own error
    }
  }, []);

  // Replies from the broker, and anything sent while this page was closed, arrive without the buyer asking, so keep listening.
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const waitingForStored = stillWaiting(messages, pending);

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [messages.length, waitingForStored.length, waiting]);

  const busy = waiting !== null;
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setWaiting((s) => (s === null ? null : s + 1)), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  async function run(shown: string, request: () => Promise<ChatReply>) {
    if (busy) return;
    setError(null);
    setQuickReplies([]);
    setWaiting(0);
    setPending((p) => [...p, { key: Date.now(), content: shown, at: new Date().toISOString(), after: lastId.current }]);
    setTimeout(() => void refresh(), 1500);
    try {
      const reply = await request();
      setTakeover(reply.takeover);
      if (reply.active_matches.length) setMatches(reply.active_matches);
      setQuickReplies(reply.quick_replies ?? []);
      if (reply.error) setError("The assistant had trouble just now. Please send that again in a moment.");
    } catch (e) {
      setError((e as Error).message);
    }
    setWaiting(null);
    await refresh();
    setPending([]); // by now the stored copies are in the list
  }

  function send(message: string) {
    const clean = message.trim();
    if (!clean) return;
    setText("");
    void run(clean, () => call<ChatReply>("/api/me/chat", { method: "POST", body: JSON.stringify({ text: clean }) }));
  }

  function openHome(action: (typeof ACTIONS)[number]) {
    if (!context) return;
    const id = context.id;
    const shown = `${action.said}: ${context.title}`.slice(0, 300);
    setContext(null);
    router.replace("/portal/chat"); // so a refresh does not ask again
    void run(shown, () => call<ChatReply>("/api/me/chat/open", { method: "POST", body: JSON.stringify({ property_id: id, action: action.action }) }));
  }

  const empty = loaded && messages.length === 0 && waitingForStored.length === 0 && !context;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
      <section aria-label="Conversation with the assistant" className="flex h-[calc(100dvh-13rem)] min-h-[32rem] flex-col overflow-hidden rounded-3xl border border-line bg-white shadow-[0_24px_50px_-34px_rgb(60_40_10/0.4)]">
        <div ref={scroller} role="log" aria-live="polite" aria-label="Messages" className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-5 sm:px-6">
          {context && (
            <div className="rounded-2xl border border-accent/30 bg-accent/5 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-accent">You are asking about</p>
              <p className="mt-1 font-medium">{context.title}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {ACTIONS.map((a) => <button key={a.action} type="button" disabled={busy} onClick={() => openHome(a)} className={buttonClass("outline", "px-4 py-2 disabled:opacity-50")}>{a.label}</button>)}
              </div>
              <p className="mt-3 text-xs text-muted">Or type your own question below.</p>
            </div>
          )}

          {empty && (
            <div className="px-2 py-10 text-center">
              <h2 className="display text-2xl font-medium">How can I help you find a home?</h2>
              <p className="mx-auto mt-2 max-w-md text-sm text-muted">Tell me your budget, the area and how many bedrooms. I answer in English and Amharic, and our broker can join any time.</p>
              <div className="mt-5 flex flex-col items-center gap-2">
                {STARTERS.map((s) => <button key={s} type="button" onClick={() => send(s)} className="max-w-md rounded-full border border-line bg-bg px-4 py-2 text-left text-sm transition hover:border-accent">{s}</button>)}
              </div>
            </div>
          )}

          {messages.map((m) => m.source === "system" ? (
            <p key={m.id} className="text-center text-xs text-muted">{m.content}</p>
          ) : (
            <Bubble key={m.id} role={m.role} time={clock(m.created_at)} text={m.content} files={chatFiles(m)} />
          ))}
          {waitingForStored.map((u) => <Bubble key={u.key} role="buyer" time={clock(u.at)} text={u.content} faded />)}

          {busy && (
            <div className="flex justify-start" role="status">
              <div className="rounded-2xl rounded-bl-md border border-line bg-bg px-4 py-2.5 text-sm text-muted">The assistant is thinking<span aria-hidden="true">...</span> <span className="tnum">{waiting}s</span>{(waiting ?? 0) >= 15 && " · this is taking longer than usual, thank you for waiting"}</div>
            </div>
          )}
        </div>

        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="border-t border-line bg-bg/60 p-3 sm:p-4">
          {takeover && <p className="mb-2 rounded-lg bg-accent/10 px-3 py-2 text-sm text-accent" role="status">Our broker has joined this conversation, so the assistant will stay quiet. They will reply to you personally.</p>}
          {quickReplies.length > 0 && !busy && (
            <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Suggested replies">
              {quickReplies.flat().map((b) => <button key={b.text} type="button" onClick={() => send(b.text)} className="rounded-full border border-accent/40 bg-accent/10 px-4 py-1.5 text-sm font-medium text-accent transition hover:bg-accent/20">{b.label}</button>)}
            </div>
          )}
          {error && <p role="alert" className="mb-2 text-sm text-bad">{error}</p>}
          <div className="flex items-end gap-2">
            <label htmlFor="chat-input" className="sr-only">Your message</label>
            <textarea id="chat-input" rows={1} value={text} maxLength={2000} placeholder="Type your message" autoComplete="off"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              className="max-h-32 min-h-[2.75rem] flex-1 resize-none rounded-2xl border border-line bg-white px-4 py-2.5 text-[15px] outline-none" />
            <button type="submit" disabled={busy || !text.trim()} className={buttonClass("brass", "h-11 px-6 disabled:opacity-50")}>Send</button>
          </div>
        </form>
      </section>

      <aside aria-label="Homes we talked about" className="lg:sticky lg:top-24 lg:self-start">
        <div className="rounded-3xl border border-line bg-white">
          <h2 className="border-b border-line px-5 py-4 text-sm font-semibold">Homes we talked about</h2>
          {matches.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted">Homes the assistant suggests will appear here, numbered the way it numbers them. Say &quot;option 1&quot; to talk about the first.</p>
          ) : (
            <ol className="divide-y divide-line">
              {matches.map((m, i) => (
                <li key={m.id}>
                  <Link href={`/portal/homes/${m.id}`} className="block px-5 py-4 transition hover:bg-bg">
                    <p className="text-sm font-medium"><span className="tnum mr-1.5 text-accent">{i + 1}.</span>{m.title}</p>
                    <p className="mt-1 text-xs text-muted tnum">{fmtEtb(m.price_etb)} · {m.bedrooms === 0 ? "Studio" : `${m.bedrooms} bed`} · {m.sub_city}</p>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}

function Bubble({ role, time, text, files = [], faded = false }: { role: WebMessage["role"]; time: string; text: string; files?: ChatFile[]; faded?: boolean }) {
  const mine = role === "buyer";
  const style = mine ? "rounded-br-md bg-ink text-white" : role === "broker" ? "rounded-bl-md border border-accent/30 bg-accent/10" : "rounded-bl-md border border-line bg-bg";
  return (
    <div className={`msg-in flex ${mine ? "justify-end" : "justify-start"} ${faded ? "opacity-70" : ""}`}>
      <div className={`max-w-[88%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed sm:max-w-[80%] ${style}`}>
        <p className={`mb-0.5 text-[11px] font-semibold ${mine ? "text-white/60" : "text-muted"}`}>{mine ? "You" : role === "broker" ? "Our broker" : "Assistant"} <time className="font-normal">{time}</time></p>
        <p className="whitespace-pre-wrap break-words">{text}</p>
        {files.length > 0 && (
          <div className="mt-3 space-y-2">
            {files.filter((f) => f.kind === "image").map((f) => (
              // eslint-disable-next-line @next/next/no-img-element -- a private, signed-in file the image optimiser cannot fetch
              <img key={f.url} src={f.url} alt={f.label} loading="lazy" className="w-full max-w-sm rounded-xl border border-line bg-white" />
            ))}
            <div className="flex flex-wrap gap-2">
              {files.filter((f) => f.kind === "download").map((f) => (
                <a key={f.url} href={f.url} download className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-white px-3.5 py-1.5 text-sm font-medium text-accent transition hover:bg-accent/10">
                  <span aria-hidden="true">↓</span>{f.label}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
