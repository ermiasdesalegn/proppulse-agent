import { describe, expect, it } from "vitest";
import { latestPerPortal, needAttention, runSummary } from "./portals";
import type { CrawlRun } from "./types";

const run = (over: Partial<CrawlRun>): CrawlRun => ({
  id: 1, portal: "guzohomes", portal_name: "GuzoHomes", request_id: null, trigger: "dashboard", status: "FINISHED", dry_run: false, mode: "limited", page_limit: 50,
  run_url: null, stats: {}, blocked_reason: null, error: null, requested_at: "2026-10-24T10:00:00Z", started_at: null, finished_at: null, waiting_too_long: false, success_rate: null, ...over,
});
const portals = [{ slug: "guzohomes" }, { slug: "addislist" }, { slug: "livingethio" }];

describe("portal runs", () => {
  it("keeps the newest run of each portal, in the portals' own order", () => {
    const latest = latestPerPortal([run({ id: 9, portal: "addislist" }), run({ id: 8, portal: "guzohomes" }), run({ id: 3, portal: "addislist", status: "FAILED" })], portals);
    expect([...latest.keys()]).toEqual(["guzohomes", "addislist"]);
    expect(latest.get("addislist")?.id).toBe(9);
  });

  it("flags only blocked, failed and never-started latest runs", () => {
    const latest = latestPerPortal([
      run({ id: 5, portal: "guzohomes", status: "BLOCKED" }), run({ id: 4, portal: "addislist", status: "QUEUED", waiting_too_long: true }),
      run({ id: 3, portal: "livingethio", status: "QUEUED" }), run({ id: 2, portal: "guzohomes", status: "FINISHED" }),
    ], portals);
    expect(needAttention(latest).map((r) => r.id)).toEqual([5, 4]);
  });

  it("says in plain words what a run did or why it did not", () => {
    expect(runSummary(run({ stats: { pages: 40, created: 5, price_changed: 2, retired: 1, skipped: 3, model_reads: 2 } }))).toBe("40 pages, 5 new, 2 price changes, 1 retired, 3 skipped, 2 Gemini calls");
    expect(runSummary(run({ status: "BLOCKED", blocked_reason: "the site answered with a bot challenge" }))).toBe("the site answered with a bot challenge");
    expect(runSummary(run({ status: "QUEUED", waiting_too_long: true }))).toContain("GitHub token");
    expect(runSummary(run({ status: "FAILED", error: null }))).toBe("The crawl failed.");
  });
});
