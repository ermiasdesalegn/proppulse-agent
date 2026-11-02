"use client";

import Link from "next/link";
import { useState } from "react";
import { errorMessage, post } from "@/lib/api";
import type { JobSummary, Stats } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Icon, type IconName } from "./icons";
import { Button, ErrorNote } from "./ui";

type JobKey = "scraper" | "alerts" | "reminders";

/** What a scheduled job answers when it was queued for the background workers instead of run (anything but a preview). */
interface QueuedJobs {
  queued: true;
  job_ids: number[];
}

const JOBS: { key: JobKey; label: string; path: string; icon: IconName }[] = [
  { key: "scraper", label: "Run Market Scraper", path: "cron/scrape-market", icon: "refresh" },
  { key: "alerts", label: "Trigger Reverse-RAG Price-Drop Alerts", path: "cron/reverse-rag-alerts", icon: "bell" },
  { key: "reminders", label: "Send 24h Tour & Milestone Reminders", path: "cron/reminders", icon: "clock" },
];

const JOB_TITLES: Record<string, string> = {
  reverse_rag_alerts: "Price-drop and new-listing alerts",
  tour_reminders: "24-hour tour reminders",
  milestone_reminders: "Payment milestones and commission",
  market_scraper: "Developer listing pages",
};

export default function ControlBar() {
  const [dryRun, setDryRun] = useState(true);
  const [running, setRunning] = useState<JobKey | null>(null);
  const [result, setResult] = useState<{ label: string; summary: JobSummary } | null>(null);
  const [queued, setQueued] = useState<{ label: string; count: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(job: (typeof JOBS)[number]) {
    setRunning(job.key);
    setError(null);
    try {
      const summary = await post<JobSummary | (JobSummary["jobs"][number]) | QueuedJobs>(`${job.path}?dry_run=${dryRun}`);
      if ("queued" in summary) {
        // A real run is handed to the background workers; the Agency page lists the jobs and how they went.
        setResult(null);
        setQueued({ label: job.label, count: summary.job_ids.length });
        return;
      }
      setQueued(null);
      // A single job answers with its own result; the combined endpoints wrap several.
      setResult({ label: job.label, summary: "jobs" in summary ? summary : { dry_run: summary.dry_run, sent: summary.sent, skipped: summary.skipped, failed: summary.failed, jobs: [summary] } });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="z-20 border-b border-[#222] bg-[#0f0f0f] md:sticky md:top-0">
      <div className="flex items-center gap-2 overflow-x-auto px-4 py-2.5 md:flex-wrap md:overflow-visible md:px-8 [&>*]:shrink-0 [&>button]:whitespace-nowrap">
        <span className="mr-1 text-xs font-semibold uppercase tracking-wider text-muted">Run now</span>
        {JOBS.map((job) => (
          <Button key={job.key} variant={job.key === "scraper" ? "default" : "glow"} disabled={running !== null} onClick={() => run(job)}>
            <span className="inline-flex items-center gap-2"><Icon name={job.icon} className="h-4 w-4 text-zinc-500" />{running === job.key ? "Running..." : job.label}</span>
          </Button>
        ))}
        <ResetDemo />
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />
          <span title="Preview who would be contacted without sending anything">Preview only</span>
        </label>
      </div>

      {(result || queued || error) && (
        <div className="border-t border-line bg-raised px-4 py-3 text-sm md:px-6">
          {error && <ErrorNote>{error}</ErrorNote>}
          {queued && (
            <div className="flex items-start justify-between gap-3">
              <p role="status" className="font-medium">
                {queued.label}: queued as <span className="tnum">{queued.count}</span> background {queued.count === 1 ? "job" : "jobs"}. Follow them on the{" "}
                <Link href="/console/agency" className="underline">Agency page</Link>.
              </p>
              <button onClick={() => setQueued(null)} className="text-muted hover:text-ink" aria-label="Dismiss result">Dismiss</button>
            </div>
          )}
          {result && (
            <div>
              <div className="flex items-start justify-between gap-3">
                <p className="font-medium">
                  {result.label}: {result.summary.dry_run ? "preview" : "done"}. <span className="tnum">{result.summary.sent}</span>{" "}
                  {result.summary.jobs[0]?.job === "market_scraper" ? (result.summary.dry_run ? "changes would be applied" : "changes applied") : result.summary.dry_run ? "would be sent" : "sent"},{" "}
                  <span className="tnum">{result.summary.skipped}</span> {result.summary.jobs[0]?.job === "market_scraper" ? "unchanged" : "skipped"}, <span className="tnum">{result.summary.failed}</span> failed.
                </p>
                <button onClick={() => setResult(null)} className="text-muted hover:text-ink" aria-label="Dismiss result">Dismiss</button>
              </div>
              <ul className="mt-2 space-y-2">
                {result.summary.jobs.map((job) => (
                  <li key={job.job}>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted">{JOB_TITLES[job.job] ?? job.job}</p>
                    {job.details.length === 0 ? (
                      <p className="text-muted">Nothing to do right now.</p>
                    ) : (
                      <ul className="mt-1 space-y-1">
                        {job.details.map((detail, i) => (
                          <li key={i} className="rounded-md border border-line bg-panel px-2 py-1 font-mono text-xs break-words">{summarise(detail)}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function summarise(detail: Record<string, unknown>): string {
  return Object.entries(detail).map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join("  |  ");
}

/** Wipes the conversations and restores the demo catalogue. Only offered when the backend allows it, and only after typing RESET. */
function ResetDemo() {
  const stats = useApi<Stats>("stats");
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!stats.data?.demo_reset_enabled) return null;

  async function reset() {
    setBusy(true); setError(null);
    try {
      await post("demo/reset", { confirm: "RESET" });
      window.location.href = "/console";
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="danger" onClick={() => { setOpen(true); setTyped(""); setError(null); }}>Reset demo</Button>
      {open && (
        <div role="dialog" aria-modal="true" aria-labelledby="reset-title" className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl border border-line bg-panel p-5 shadow-xl">
            <h2 id="reset-title" className="text-base font-semibold">Reset the demo?</h2>
            <p className="mt-2 text-sm text-muted">This deletes every buyer, conversation, booking, deal, alert and log, cancels the calendar events the system created, puts every unit back to its original price, and removes units that were added by the scraper. It cannot be undone.</p>
            <label htmlFor="reset-confirm" className="mt-4 block text-sm font-medium">Type RESET to confirm</label>
            <input id="reset-confirm" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" autoFocus
              className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent" />
            {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
            <div className="mt-4 flex justify-end gap-2">
              <Button onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
              <Button variant="danger" disabled={busy || typed !== "RESET"} onClick={() => void reset()}>{busy ? "Resetting..." : "Reset everything"}</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
