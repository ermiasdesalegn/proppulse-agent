// Helpers for the Agency page: channel status, usage against the plan, and the audit trail in plain words.

export interface ChannelStatus {
  telegram: { connected: boolean; bot_username: string | null; connected_at: string | null; webhook_url: string | null };
  calendar: { connected: boolean; calendar_id: string | null };
  settings: { broker_name: string | null; persona: string | null; broker_chat_id: string | null; timezone: string | null };
}

export interface Usage {
  plan: string;
  daily_message_limit: number;
  buyer_messages_today: number;
  model_calls_today: number;
  remaining: number;
  over_limit: boolean;
}

export interface AuditEntry {
  id: number;
  actor_kind: "member" | "platform" | "system";
  actor_id: string | null;
  action: string;
  target: string | null;
  detail: Record<string, unknown>;
  created_at: string;
}

/** The shape BotFather hands out; the same rule the backend applies, so a typo is caught before anything is sent. */
export const BOT_TOKEN_SHAPE = /^\d{6,12}:[A-Za-z0-9_-]{30,60}$/;

export function looksLikeBotToken(text: string): boolean {
  return BOT_TOKEN_SHAPE.test(text.trim());
}

/** Whether a pasted key file could be a Google service-account key (the backend checks again, and Google decides in the end). */
export function looksLikeServiceAccount(text: string): boolean {
  try {
    const parsed: unknown = JSON.parse(text);
    return !!parsed && typeof parsed === "object" && "client_email" in parsed && "private_key" in parsed;
  } catch {
    return false;
  }
}

/** 0 to 100, for a progress bar. A plan with no limit shows as empty rather than dividing by zero. */
export function usagePercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

export function usageTone(usage: Usage): "good" | "warn" | "bad" {
  if (usage.over_limit) return "bad";
  return usagePercent(usage.buyer_messages_today, usage.daily_message_limit) >= 80 ? "warn" : "good";
}

const ACTION_LABEL: Record<string, string> = {
  "org.create": "Created the agency",
  "member.role_change": "Changed a member's role",
  "member.remove": "Removed a member",
  "member.leave": "A member left",
  "invitation.create": "Invited someone",
  "invitation.revoke": "Cancelled an invitation",
  "invitation.accept": "Accepted an invitation",
  "channel.telegram_connect": "Connected a Telegram bot",
  "channel.telegram_disconnect": "Disconnected the Telegram bot",
  "channel.calendar_connect": "Connected a calendar",
  "channel.calendar_disconnect": "Disconnected the calendar",
  "settings.change": "Changed settings",
};

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

/** One line for the trail: what happened, to what, and the roles involved when there were any. */
export function describeEntry(entry: AuditEntry): string {
  const parts = [actionLabel(entry.action)];
  if (entry.target) parts.push(entry.target);
  const { old_role: from, new_role: to, role } = entry.detail as { old_role?: string; new_role?: string; role?: string };
  if (from && to) parts.push(`(${from} to ${to})`);
  else if (role) parts.push(`(${role})`);
  return parts.join(" ");
}

export function actorLabel(entry: AuditEntry): string {
  if (entry.actor_kind === "platform") return "Platform operator";
  if (entry.actor_kind === "system") return "System";
  return entry.actor_id ? `Member ${entry.actor_id.slice(0, 8)}` : "A member";
}
