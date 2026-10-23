"use client";

import { useState } from "react";
import { errorMessage, post } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { Button, ErrorNote } from "./ui";

type JobKey = "scraper" | "alerts" | "reminders";

const JOBS: { key: JobKey; label: string; path: string }[] = [
  { key: "scraper", label: "Run Market Scraper", path: "cron/scrape-market" },
  { key: "alerts", label: "Trigger Reverse-RAG Price-Drop Alerts", path: "cron/reverse-rag-alerts" },
  { key: "reminders", label: "Send 24h Tour & Milestone Reminders", path: "cron/reminders" },
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
  const [error, setError] = useState<string | null>(null);

  async function run(job: (typeof JOBS)[number]) {
    setRunning(job.key);
    setError(null);
    try {
      const summary = await post<JobSummary | (JobSummary["jobs"][number])>(`${job.path}?dry_run=${dryRun}`);
      // A single job answers with its own result; the combined endpoints wrap several.
      setResult({ label: job.label, summary: "jobs" in summary ? summary : { dry_run: summary.dry_run, sent: summary.sent, skipped: summary.skipped, failed: summary.failed, jobs: [summary] } });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="border-b border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 md:px-6">
        <span className="mr-1 text-xs font-semibold uppercase tracking-wider text-muted">Run now</span>
        {JOBS.map((job) => (
          <Button key={job.key} variant={job.key === "scraper" ? "default" : "primary"} disabled={running !== null} onClick={() => run(job)}>
            {running === job.key ? "Running..." : job.label}
          </Button>
        ))}
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />
          <span title="Preview who would be contacted without sending anything">Preview only</span>
        </label>
      </div>

      {(result || error) && (
        <div className="border-t border-line bg-raised px-4 py-3 text-sm md:px-6">
          {error && <ErrorNote>{error}</ErrorNote>}
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
