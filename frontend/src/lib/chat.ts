// Types and small rules for the buyer's chat with the assistant.

export interface WebMessage {
  id: number;
  role: "buyer" | "agent" | "broker";
  source: "text" | "voice" | "system";
  content: string;
  created_at: string;
}

export interface QuickReply {
  label: string;
  text: string;
}

export interface MatchRef {
  id: string;
  title: string;
  sub_city: string;
  price_etb: number;
  bedrooms: number;
  similarity: number;
}

export interface ChatReply {
  reply: string | null;
  quick_replies: QuickReply[][];
  takeover: boolean;
  error: boolean;
  active_matches: MatchRef[];
}

export interface Polled {
  takeover: boolean;
  messages: WebMessage[];
}

/** A message the buyer has sent that the stored transcript has not caught up with yet. `after` is the newest stored id at the moment of sending. */
export interface Unconfirmed {
  key: number;
  content: string;
  at: string;
  after: number;
}

/** Adds new messages to the ones already shown, once each, in order. */
export function mergeMessages(shown: WebMessage[], incoming: WebMessage[]): WebMessage[] {
  const known = new Set(shown.map((m) => m.id));
  const fresh = incoming.filter((m) => !known.has(m.id));
  return fresh.length ? [...shown, ...fresh].sort((a, b) => a.id - b.id) : shown;
}

/**
 * The buyer's own message is shown the moment it is sent. It stops being shown separately once the stored copy arrives, which is a buyer
 * message with the same words that is newer than what was stored when it was sent. Saying the same thing twice therefore still shows twice.
 */
export function stillWaiting(messages: WebMessage[], pending: Unconfirmed[]): Unconfirmed[] {
  const claimed = new Set<number>();
  return pending.filter((p) => {
    const stored = messages.find((m) => m.role === "buyer" && m.id > p.after && m.content === p.content && !claimed.has(m.id));
    if (stored) claimed.add(stored.id);
    return !stored;
  });
}

export function newestId(messages: WebMessage[]): number {
  return messages.reduce((max, m) => Math.max(max, m.id), 0);
}

export function clock(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Addis_Ababa", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

export const STARTERS = [
  "Hi! I'm looking for a 3 bedroom apartment in Bole, around 25 million birr.",
  "What can I get for 10 million birr?",
  "ሰላም! በቦሌ ባለ 2 መኝታ አፓርታማ እፈልጋለሁ።",
];
