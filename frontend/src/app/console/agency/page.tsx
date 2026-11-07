"use client";

import { useState } from "react";
import { Button, Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import {
  actorLabel, canRetry, describeEntry, dollars, JOB_STATUSES, jobLabel, jobOutcome, jobTone, keySummary, keyTone, looksLikeBotToken, looksLikeServiceAccount, usagePercent, usageTone,
  type AuditEntry, type ChannelStatus, type JobList, type KeyStatus, type Usage,
} from "@/lib/agency";
import { api, errorMessage, patch, post, put } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { ORG_RENAMED } from "@/lib/org";
import { agencyNameBody } from "@/lib/onboarding";
import { useApi } from "@/lib/useApi";

const BAR: Record<"good" | "warn" | "bad", string> = { good: "bg-good", warn: "bg-warn", bad: "bg-bad" };

export default function AgencyPage() {
  const channels = useApi<ChannelStatus>("channels");
  const usage = useApi<Usage>("usage", 30_000);
  const audit = useApi<{ entries: AuditEntry[] }>("audit?limit=50");

  return (
    <div className="space-y-6">
      <PageHeader
        title={channels.data?.name ?? "Agency"}
        description="Connect this agency's own Telegram bot and calendar, set how the assistant introduces you, see today's use against your plan and the background jobs, and read who changed what."
      />
      {channels.error && <ErrorNote>{channels.error}</ErrorNote>}
      <NameCard name={channels.data?.name} slug={channels.data?.slug} onSaved={() => { void channels.reload(); void audit.reload(); window.dispatchEvent(new Event(ORG_RENAMED)); }} />
      <UsageCard usage={usage.data} error={usage.error} />
      <div className="grid gap-6 xl:grid-cols-2">
        <TelegramCard status={channels.data?.telegram} onChanged={() => { void channels.reload(); void audit.reload(); }} />
        <CalendarCard status={channels.data?.calendar} onChanged={() => { void channels.reload(); void audit.reload(); }} />
      </div>
      <SettingsCard settings={channels.data?.settings} onSaved={() => { void channels.reload(); void audit.reload(); }} />
      <TeamCard onInvited={() => void audit.reload()} />
      <JobsCard onRetried={() => void audit.reload()} />
      <KeysCard />
      <Card title="Change history" subtitle="Roles, invitations, channels and settings. Only admins can read this.">
        {audit.error ? <ErrorNote>{audit.error}</ErrorNote> : !audit.data ? <Empty>Loading...</Empty> : audit.data.entries.length === 0 ? <Empty>Nothing has been changed yet.</Empty> : (
          <ul>
            {audit.data.entries.map((e) => (
              <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-[#222] px-5 py-2.5 text-sm">
                <span className="break-words">{describeEntry(e)}</span>
                <span className="text-xs text-muted">{actorLabel(e)} · {timeAgo(e.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** The operator only (others get 403 and see nothing): which master key seals how many agencies' data keys. */
function KeysCard() {
  const keys = useApi<KeyStatus>("keys/status", 30_000);
  if (!keys.data) return null;
  const k = keys.data;
  return (
    <Card title="Master key" subtitle="Ids only. Rotate with python -m scripts.rotate_master_key (see SECURITY.md)." action={<Pill tone={keyTone(k)}>{k.done ? "Up to date" : "Rotating"}</Pill>}>
      <div className="space-y-2 px-5 pb-5 text-sm">
        <p role="status">{keySummary(k)}</p>
        <ul className="flex flex-wrap gap-2 text-xs text-muted">
          {Object.entries(k.by_key).map(([id, n]) => <li key={id} className="rounded-md border border-line px-2 py-0.5 font-mono">{id}{id === k.current ? " (newest)" : ""}: {n}</li>)}
        </ul>
      </div>
    </Card>
  );
}

function JobsCard({ onRetried }: { onRetried: () => void }) {
  const jobs = useApi<JobList>("jobs?limit=30", 10_000);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function retry(id: number) {
    setBusy(id); setError(null);
    try { await post(`jobs/${id}/retry`); await jobs.reload(); onRetried(); } catch (e) { setError(errorMessage(e)); }
    setBusy(null);
  }

  const data = jobs.data;
  return (
    <Card
      title="Background jobs"
      subtitle={data?.operator ? "Every agency's alerts, reminders and offers, as the workers run them." : "This agency's alerts, reminders and offers, as the workers run them. Admins only."}
      action={data ? <span className="flex flex-wrap gap-1">{JOB_STATUSES.filter((s) => data.counts[s] > 0).map((s) => <Pill key={s} tone={jobTone(s)}>{data.counts[s]} {s}</Pill>)}</span> : undefined}
    >
      {jobs.error ? <div className="px-5 pb-5"><ErrorNote>{jobs.error}</ErrorNote></div> : !data ? <Empty>Loading...</Empty> : data.jobs.length === 0 ? <Empty>No job has been queued yet.</Empty> : (
        <ul>
          {data.jobs.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-[#222] px-5 py-2.5 text-sm">
              <span className="min-w-0 break-words">
                <span className="font-medium">{jobLabel(j.kind)}</span>
                {data.operator && <span className="text-muted"> · {j.organization}</span>}
                <span className="block text-xs text-muted">{jobOutcome(j)}{j.last_error && j.status !== "done" ? `: ${j.last_error}` : ""}</span>
              </span>
              <span className="flex items-center gap-2 text-xs text-muted">
                <Pill tone={jobTone(j.status)}>{j.status}</Pill>
                {timeAgo(j.finished_at ?? j.started_at ?? j.created_at)}
                {canRetry(j, data.operator) && <Button disabled={busy !== null} onClick={() => void retry(j.id)}>{busy === j.id ? "Retrying..." : "Retry"}</Button>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {error && <div className="px-5 pb-5"><ErrorNote>{error}</ErrorNote></div>}
    </Card>
  );
}

function UsageCard({ usage, error }: { usage: Usage | null; error: string | null }) {
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!usage) return <Card title="Today's use"><Empty>Loading...</Empty></Card>;
  const tone = usageTone(usage);
  const percent = usagePercent(usage.buyer_messages_today, usage.daily_message_limit);
  const budgetPercent = usagePercent(usage.month_spend_usd, usage.monthly_budget_usd);
  return (
    <Card title="Today's use" subtitle="Counted from midnight UTC" action={<Pill tone={tone}>{usage.plan} plan</Pill>}>
      <div className="space-y-2 px-5 pb-5">
        <p className="text-sm">
          <span className="text-2xl font-semibold">{usage.buyer_messages_today}</span>
          <span className="text-muted"> of {usage.daily_message_limit} buyer messages answered · {usage.model_calls_today} model calls</span>
        </p>
        <div className="h-2 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label="Daily messages used">
          <div className={`h-full ${BAR[tone]}`} style={{ width: `${percent}%` }} />
        </div>
        {usage.over_limit && <p role="status" className="text-sm text-bad">The limit is reached. Buyers are told the broker will reply, and their messages wait in the CRM.</p>}
        <p className="pt-2 text-sm">
          <span className="text-lg font-semibold">{dollars(usage.month_spend_usd)}</span>
          <span className="text-muted"> of {dollars(usage.monthly_budget_usd)} model budget this month · {usage.jobs_today} of {usage.daily_job_limit} background jobs today</span>
        </p>
        <div className="h-2 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuenow={budgetPercent} aria-valuemin={0} aria-valuemax={100} aria-label="Monthly model budget used">
          <div className={`h-full ${BAR[usage.over_budget ? "bad" : budgetPercent >= 80 ? "warn" : "good"]}`} style={{ width: `${budgetPercent}%` }} />
        </div>
        <p className="text-xs text-muted">Spend is the tokens recorded for each call times prices set by the operator ({usage.prices_as_of}).</p>
        {usage.over_budget && <p role="status" className="text-sm text-bad">The monthly budget is reached. Buyers are told the broker will reply, and their messages wait in the CRM.</p>}
      </div>
    </Card>
  );
}

function TelegramCard({ status, onChanged }: { status: ChannelStatus["telegram"] | undefined; onChanged: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setBusy(true); setError(null); setNote(null);
    try {
      const result = await put<{ webhook?: string }>("channels/telegram", { bot_token: token.trim() });
      setToken("");
      setNote(result.webhook ? `Connected. Webhook ${result.webhook}.` : "Connected.");
      onChanged();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  async function disconnect() {
    setBusy(true); setError(null); setNote(null);
    try { await api("channels/telegram", { method: "DELETE" }); setNote("Disconnected."); onChanged(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  const ready = looksLikeBotToken(token);
  return (
    <Card id="telegram" title="Telegram bot" subtitle="Buyers message this bot; replies come from it." action={status ? <Pill tone={status.connected ? "good" : "neutral"}>{status.connected ? "Connected" : "Not connected"}</Pill> : undefined}>
      <div className="space-y-3 px-5 pb-5 text-sm">
        {status?.connected && <p className="text-muted">@{status.bot_username}{status.connected_at ? `, connected ${timeAgo(status.connected_at)}` : ""}</p>}
        <label className="block"><span className="mb-1 block text-muted">{status?.connected ? "Replace the bot" : "Bot token from BotFather"}</span>
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456789:AA..." className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2" aria-label="Bot token" />
        </label>
        <p className="text-xs text-muted">The token is encrypted before it is stored and is never shown again.</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy || !ready} onClick={() => void connect()}>{busy ? "Working..." : status?.connected ? "Replace" : "Connect"}</Button>
          {status?.connected && <Button variant="danger" disabled={busy} onClick={() => void disconnect()}>Disconnect</Button>}
        </div>
        {token && !ready && <p className="text-xs text-warn">That does not look like a bot token yet (numbers, a colon, then letters and digits).</p>}
        {note && <p role="status" className="text-muted">{note}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function CalendarCard({ status, onChanged }: { status: ChannelStatus["calendar"] | undefined; onChanged: () => void }) {
  const [key, setKey] = useState("");
  const [calendarId, setCalendarId] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setBusy(true); setError(null); setNote(null);
    try {
      await put("channels/calendar", { service_account_json: key, calendar_id: calendarId.trim() });
      setKey(""); setCalendarId(""); setNote("Connected."); onChanged();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  async function disconnect() {
    setBusy(true); setError(null); setNote(null);
    try { await api("channels/calendar", { method: "DELETE" }); setNote("Disconnected."); onChanged(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  const ready = looksLikeServiceAccount(key) && calendarId.trim().length >= 3;
  return (
    <Card title="Google Calendar" subtitle="Bookings are checked against it and added to it." action={status ? <Pill tone={status.connected ? "good" : "neutral"}>{status.connected ? "Connected" : "Not connected"}</Pill> : undefined}>
      <div className="space-y-3 px-5 pb-5 text-sm">
        {status?.connected && <p className="break-all text-muted">{status.calendar_id}</p>}
        <label className="block"><span className="mb-1 block text-muted">Calendar address</span>
          <input value={calendarId} onChange={(e) => setCalendarId(e.target.value)} placeholder="you@gmail.com" className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2" aria-label="Calendar address" />
        </label>
        <label className="block"><span className="mb-1 block text-muted">Service-account key (the whole JSON file)</span>
          <textarea value={key} onChange={(e) => setKey(e.target.value)} rows={4} spellCheck={false} autoComplete="off" className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2 font-mono text-xs" aria-label="Service-account key" />
        </label>
        <p className="text-xs text-muted">Share the calendar with the key&apos;s client_email and choose &quot;Make changes to events&quot;. The key is encrypted before it is stored and never shown again.</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy || !ready} onClick={() => void connect()}>{busy ? "Working..." : status?.connected ? "Replace" : "Connect"}</Button>
          {status?.connected && <Button variant="danger" disabled={busy} onClick={() => void disconnect()}>Disconnect</Button>}
        </div>
        {key && !looksLikeServiceAccount(key) && <p className="text-xs text-warn">Paste the whole key file: it needs client_email and private_key.</p>}
        {note && <p role="status" className="text-muted">{note}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function SettingsCard({ settings, onSaved }: { settings: ChannelStatus["settings"] | undefined; onSaved: () => void }) {
  const [draft, setDraft] = useState<Partial<ChannelStatus["settings"]>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const value = (k: keyof ChannelStatus["settings"]) => draft[k] ?? settings?.[k] ?? "";
  const changed = Object.keys(draft).length > 0;

  async function save() {
    setBusy(true); setError(null); setNote(null);
    try { await patch("org/settings", draft); setDraft({}); setNote("Saved."); onSaved(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  const field = (k: keyof ChannelStatus["settings"], label: string, placeholder = "") => (
    <label className="block text-sm"><span className="mb-1 block text-muted">{label}</span>
      <input value={value(k)} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} placeholder={placeholder} className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2" aria-label={label} />
    </label>
  );
  return (
    <Card title="How the assistant introduces you" subtitle="Used in replies and in the notices sent to you.">
      <div className="space-y-3 px-5 pb-5">
        <div className="grid gap-3 sm:grid-cols-2">
          {field("broker_name", "Name buyers see", "Ermias")}
          {field("timezone", "Time zone", "Africa/Addis_Ababa")}
          {field("broker_chat_id", "Your Telegram chat id (for notices)", "123456789")}
          {field("persona", "A line about your style (optional)", "Friendly and direct")}
        </div>
        <div className="flex items-center gap-3">
          <Button variant="primary" disabled={busy || !changed} onClick={() => void save()}>{busy ? "Saving..." : "Save"}</Button>
          {note && <span role="status" className="text-sm text-muted">{note}</span>}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function NameCard({ name, slug, onSaved }: { name: string | undefined; slug: string | undefined; onSaved: () => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const value = draft ?? name ?? "";
  const checked = agencyNameBody(value);
  const changed = draft !== null && "name" in checked && checked.name !== name;

  async function save() {
    if (!("name" in checked)) return;
    setBusy(true); setError(null); setNote(null);
    try { await patch("org/name", { name: checked.name }); setDraft(null); setNote("Renamed."); onSaved(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  return (
    <Card title="Agency name" subtitle="Shown in the console, on your public page and in emails. Your address stays the same.">
      <div className="space-y-3 px-5 pb-5 text-sm">
        <label className="block"><span className="mb-1 block text-muted">Name</span>
          <input value={value} onChange={(e) => setDraft(e.target.value)} maxLength={80} className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2" aria-label="Agency name" />
        </label>
        {slug && <p className="text-xs text-muted">Address: {slug}</p>}
        {draft !== null && "error" in checked && <p className="text-xs text-warn">{checked.error}</p>}
        <div className="flex items-center gap-3">
          <Button variant="primary" disabled={busy || !changed} onClick={() => void save()}>{busy ? "Saving..." : "Rename"}</Button>
          {note && <span role="status" className="text-muted">{note}</span>}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function TeamCard({ onInvited }: { onInvited: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("agent");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ready = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());

  async function invite() {
    setBusy(true); setError(null); setNote(null); setLink(null);
    try {
      const r = await post<{ emailed: boolean; token?: string; note?: string }>("invitations", { email: email.trim(), role });
      setEmail("");
      if (r.emailed) setNote("Invitation emailed.");
      else { setNote(r.note ?? "Copy the link to the person yourself."); setLink(r.token ? `${window.location.origin}/join?token=${r.token}` : null); }
      onInvited();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  return (
    <Card id="team" title="Invite a teammate" subtitle="They join with a link that only works for their email address.">
      <div className="space-y-3 px-5 pb-5 text-sm">
        <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="colleague@example.com" className="w-full rounded-lg border border-white/10 bg-transparent px-3 py-2" aria-label="Teammate email" />
          <select value={role} onChange={(e) => setRole(e.target.value)} className="rounded-lg border border-white/10 bg-[#111] px-3 py-2" aria-label="Role">
            <option value="admin">Admin</option><option value="agent">Agent</option><option value="viewer">Viewer</option>
          </select>
        </div>
        <Button variant="primary" disabled={busy || !ready} onClick={() => void invite()}>{busy ? "Inviting..." : "Send invitation"}</Button>
        {note && <p role="status" className="text-muted">{note}</p>}
        {link && <p className="break-all rounded-lg border border-white/10 px-3 py-2 font-mono text-xs">{link}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
