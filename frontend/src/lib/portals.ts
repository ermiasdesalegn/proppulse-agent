import type { CrawlRun, CrawlStatus } from "./types";

export const STATUS_LABEL: Record<CrawlStatus, { tone: "good" | "warn" | "bad" | "neutral" | "accent"; label: string }> = {
  QUEUED: { tone: "neutral", label: "Waiting for the runner" }, RUNNING: { tone: "accent", label: "Running" },
  FINISHED: { tone: "good", label: "Finished" }, BLOCKED: { tone: "warn", label: "Blocked by the site" }, FAILED: { tone: "bad", label: "Failed" },
};

/** The most recent run of each portal, in the order the portals are listed. */
export function latestPerPortal(runs: CrawlRun[], portals: { slug: string }[]): Map<string, CrawlRun> {
  const latest = new Map<string, CrawlRun>();
  for (const run of runs) if (!latest.has(run.portal)) latest.set(run.portal, run); // runs arrive newest first
  return new Map(portals.filter((p) => latest.has(p.slug)).map((p) => [p.slug, latest.get(p.slug) as CrawlRun]));
}

/** Runs that need a person: blocked, failed, or queued so long that nothing picked them up. Only the latest run of a portal counts. */
export function needAttention(latest: Map<string, CrawlRun>): CrawlRun[] {
  return [...latest.values()].filter((r) => r.status === "BLOCKED" || r.status === "FAILED" || r.waiting_too_long);
}

export function runSummary(run: CrawlRun): string {
  const s = run.stats;
  if (run.status === "QUEUED") return run.waiting_too_long ? "Nothing picked this up. Check the GitHub token and that the workflow is enabled." : "Asked GitHub to start it.";
  if (run.status === "BLOCKED") return run.blocked_reason ?? "The site refused the crawler.";
  if (run.status === "FAILED") return run.error ?? "The crawl failed.";
  if (run.status === "RUNNING") return "Reading pages...";
  const parts = [`${s.pages ?? 0} pages`, `${s.created ?? 0} new`, `${s.price_changed ?? 0} price changes`];
  if (s.retired) parts.push(`${s.retired} retired`);
  if (s.skipped) parts.push(`${s.skipped} skipped`);
  parts.push(`${s.model_reads ?? 0} Gemini calls`);
  return parts.join(", ");
}

export const MODE_LABEL = { full: "Full crawl", incremental: "New and changed", limited: "Limited" } as const;
