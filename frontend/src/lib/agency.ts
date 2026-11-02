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
  "job.retry": "Retried a background job",
  "price.override_set": "Set the agency's own price",
  "price.override_clear": "Went back to the shared price",
  "key.rewrap": "Re-sealed the agency's data key under the new master key",
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

// ---- background jobs (the queue in backend/app/jobs.py) ----

export type JobStatus = "queued" | "running" | "done" | "failed" | "dead";

export interface Job {
  id: number;
  org_id: string;
  organization: string;
  kind: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  run_after: string;
  last_error: string | null;
  result: { sent?: number; skipped?: number; failed?: number; status?: string } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobList {
  operator: boolean;
  counts: Record<JobStatus, number>;
  jobs: Job[];
}

export const JOB_STATUSES: JobStatus[] = ["queued", "running", "done", "failed", "dead"];

const JOB_LABEL: Record<string, string> = {
  "proactive.daily": "Daily proactive run",
  "proactive.reverse_rag": "Price-drop and new-listing alerts",
  "proactive.tour_reminders": "24-hour tour reminders",
  "proactive.milestone_reminders": "Payment milestones and commission",
  "projects.promotion_ads": "Developers' offers",
  "prices.project_changed": "Project price notices",
  "keys.rewrap": "Master-key re-wrap",
};

export function jobLabel(kind: string): string {
  return JOB_LABEL[kind] ?? kind;
}

export function jobTone(status: JobStatus): "good" | "warn" | "bad" | "neutral" {
  return status === "done" ? "good" : status === "dead" ? "bad" : status === "failed" ? "warn" : "neutral";
}

/** Only the operator may retry, and only a job that has stopped trying (dead) or is waiting for its next try (failed). */
export function canRetry(job: Job, operator: boolean): boolean {
  return operator && (job.status === "dead" || job.status === "failed");
}

/** One line about the outcome: what was sent, or why it is waiting. */
export function jobOutcome(job: Job): string {
  if (job.status === "done") {
    const r = job.result ?? {};
    if (r.sent === undefined && r.status) return r.status; // a job that sends nothing (a key re-wrap) says what it did
    return `${r.sent ?? 0} sent, ${r.skipped ?? 0} skipped${r.failed ? `, ${r.failed} failed` : ""}`;
  }
  if (job.status === "failed") return `attempt ${job.attempts} of ${job.max_attempts} failed; retrying`;
  if (job.status === "dead") return `gave up after ${job.attempts} attempts`;
  if (job.status === "running") return `running (attempt ${job.attempts})`;
  return "waiting for a worker";
}

// ---- master-key rotation (backend app/keys.py), seen by the operator ----

export interface KeyStatus {
  configured: boolean;
  agencies: number;
  current: string | null;
  loaded: string[];
  by_key: Record<string, number>;
  unknown: number;
  done: boolean;
}

/** One sentence on where a rotation stands. */
export function keySummary(k: KeyStatus): string {
  if (!k.configured) return "No master key is set, so agencies cannot store credentials.";
  if (k.unknown > 0) return `${k.unknown} agencies have a data key no loaded master key opens. Put the old key back before anything else.`;
  const behind = k.agencies - (k.current ? k.by_key[k.current] ?? 0 : 0);
  if (behind > 0) return `Rotation in progress: ${behind} of ${k.agencies} agencies are still on an older key. Both keys must stay loaded.`;
  if (k.loaded.length > 1) return `Every agency is on ${k.current}. The older keys (${k.loaded.slice(1).join(", ")}) can now be removed.`;
  return `Every agency is on ${k.current}.`;
}

export function keyTone(k: KeyStatus): "good" | "warn" | "bad" {
  if (!k.configured || k.unknown > 0) return "bad";
  return k.done ? "good" : "warn";
}
