"use client";

import { useState } from "react";
import Select from "@/components/Select";
import { Button, Card, Empty, ErrorNote, PageHeader, Pill, Stat } from "@/components/ui";
import { errorMessage, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { latestPerPortal, MODE_LABEL, needAttention, runSummary, STATUS_LABEL } from "@/lib/portals";
import type { CrawlRun, PortalRuns } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const SIZES = [
  { value: "50", label: "A quick look (50 pages per portal)" },
  { value: "300", label: "The newest 300 pages per portal" },
  { value: "0", label: "Everything (a full crawl, can take hours)" },
];

export default function PortalsPage() {
  const runs = useApi<PortalRuns>("portal/runs", 10_000);
  const portals = runs.data?.config.portals ?? [];
  const list = runs.data?.runs ?? [];
  const latest = latestPerPortal(list, portals);
  const attention = needAttention(latest);
  const active = list.filter((r) => r.status === "RUNNING" || (r.status === "QUEUED" && !r.waiting_too_long)).length;
  const lastFinished = list.find((r) => r.status === "FINISHED" && !r.dry_run);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Portals"
        description="Large listing sites (Ethiopia Property Centre, Live Ethio, GuzoHomes and others) are read by a crawler that runs for free on GitHub. Start it here, watch it, and you are told on Telegram if a site blocks it or stops parsing."
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Portals" icon="listing" value={portals.length} hint="Read by the crawler" />
        <Stat label="Running now" icon="analytics" value={active} hint="Started or waiting for the runner" />
        <Stat label="Need a look" icon="bell" value={attention.length} hint="Blocked, failed or never started" />
        <Stat label="Last saved crawl" icon="money" value={lastFinished?.finished_at ? timeAgo(lastFinished.finished_at) : "-"} hint={lastFinished ? lastFinished.portal_name : "Nothing saved yet"} />
      </div>

      {runs.error && <ErrorNote>{runs.error}</ErrorNote>}
      <StartCard config={runs.data?.config} onStarted={() => void runs.reload()} />

      <Card title="Each portal" subtitle="The latest run of every portal">
        {runs.loading ? <Empty>Loading...</Empty> : latest.size === 0 ? <Empty>No crawl has run yet. Start a preview above.</Empty> : (
          <ul>{portals.filter((p) => latest.has(p.slug)).map((p) => <RunRow key={p.slug} run={latest.get(p.slug) as CrawlRun} />)}</ul>
        )}
      </Card>

      <Card title="History" subtitle="Every run, newest first">
        {list.length === 0 ? <Empty>Nothing yet.</Empty> : <ul>{list.map((r) => <RunRow key={r.id} run={r} compact />)}</ul>}
      </Card>
    </div>
  );
}

function StartCard({ config, onStarted }: { config: PortalRuns["config"] | undefined; onStarted: () => void }) {
  const [site, setSite] = useState("all");
  const [size, setSize] = useState("50");
  const [save, setSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canStart = config?.can_start ?? false;
  const sites = [{ value: "all", label: "All portals" }, ...(config?.portals ?? []).map((p) => ({ value: p.slug, label: p.name }))];

  async function start() {
    setBusy(true); setError(null); setNote(null);
    try {
      await post("portal/runs", { site, limit: Number(size), dry_run: !save });
      setNote("Requested. GitHub usually starts it within a minute; the rows below update by themselves.");
      onStarted();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Card title="Run the crawler" subtitle="A preview reads the pages and reports what it would change, saving nothing.">
      <div className="space-y-4 px-5 pb-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm"><span className="mb-1 block text-muted">Portal</span><Select value={site} onChange={setSite} options={sites} ariaLabel="Portal" /></label>
          <label className="block text-sm"><span className="mb-1 block text-muted">How much</span><Select value={size} onChange={setSize} options={SIZES} ariaLabel="How much to read" /></label>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />Save what it finds (otherwise preview only)</label>
          <Button variant="primary" disabled={busy || !canStart} onClick={() => void start()}>{busy ? "Starting..." : save ? "Run and save" : "Run a preview"}</Button>
        </div>
        {!canStart && config && (
          <p role="status" className="text-sm text-muted">Starting from here needs <code>GITHUB_TOKEN</code> and <code>GITHUB_REPO</code> on the backend (Render). Scheduled runs keep working without them; see <code>backend/scraper/README.md</code>.</p>
        )}
        {note && <p role="status" className="text-sm text-muted">{note}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}

function RunRow({ run, compact = false }: { run: CrawlRun; compact?: boolean }) {
  const status = run.waiting_too_long ? { tone: "warn" as const, label: "Not picked up" } : STATUS_LABEL[run.status];
  const when = run.finished_at ?? run.started_at ?? run.requested_at;
  const rate = run.success_rate;
  return (
    <li className="border-t border-[#222] px-5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{run.portal_name}</span>
          <Pill tone={status.tone}>{status.label}</Pill>
          {run.dry_run && <Pill>Preview</Pill>}
          {!compact && <Pill>{MODE_LABEL[run.mode]}</Pill>}
          {compact && <Pill>{run.trigger === "dashboard" ? "From here" : run.trigger === "schedule" ? "Scheduled" : "By hand"}</Pill>}
        </div>
        <div className="flex items-center gap-3 text-sm text-muted">
          <span>{timeAgo(when)}</span>
          {run.run_url && <a className="underline underline-offset-2 hover:text-white" href={run.run_url} target="_blank" rel="noreferrer">GitHub log</a>}
        </div>
      </div>
      <p className="mt-1 break-words text-sm text-muted">{runSummary(run)}</p>
      {run.status === "FINISHED" && rate != null && (
        <p className="mt-1 text-xs text-muted">{Math.round(rate * 100)}% of pages had a price read without the language model{rate < 0.7 ? " (low: the site's layout may have changed)" : ""}.</p>
      )}
    </li>
  );
}
