"use client";

import { useRef, useState } from "react";
import clsx from "./clsx";
import Select from "./Select";
import { Button, Card, ErrorNote, Pill } from "./ui";
import { api, errorMessage, post } from "@/lib/api";
import { fmtEtb, fmtPct } from "@/lib/format";
import type { Developer, IngestReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Source = "html" | "text";
type Plan = { name: string; mime: string; base64: string };
const PLAN_TYPES = ["image/png", "image/jpeg", "image/webp"];
const PLAN_MAX_BYTES = 5_000_000;

function readPlan(file: File): Promise<Plan> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => resolve({ name: file.name, mime: file.type, base64: String(reader.result).split(",")[1] ?? "" });
    reader.readAsDataURL(file);
  });
}

interface Sample { developer_slug: string; html: string; profile: Record<string, unknown>; variant: number }

function Section({ title, tone, items }: { title: string; tone: "good" | "warn" | "bad" | "neutral"; items: React.ReactNode[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted"><Pill tone={tone}>{items.length}</Pill>{title}</p>
      <ul className="space-y-1 text-sm">{items.map((item, i) => <li key={i} className="rounded-md border border-line bg-raised px-3 py-1.5">{item}</li>)}</ul>
    </div>
  );
}

export function ReportView({ report }: { report: IngestReport }) {
  const c = report.counts;
  return (
    <div className="space-y-3" role="status">
      <p className="text-sm font-medium">
        {report.dry_run ? "Preview, nothing was changed." : "Applied."} Found {report.found} unit{report.found === 1 ? "" : "s"}:
        {" "}<span className="tnum">{c.created}</span> new, <span className="tnum">{c.price_changed}</span> price changes, <span className="tnum">{c.removed}</span> removed, <span className="tnum">{c.unchanged}</span> unchanged{c.skipped ? `, ${c.skipped} skipped` : ""}.
      </p>
      <Section title="New units" tone="good" items={report.created.map((u) => <span key={u.title}>{u.title} <span className="text-muted">- {u.area}, {fmtEtb(u.price_etb)}{u.floor_plan_tags?.length ? `, plan: ${u.floor_plan_tags.join(", ")}` : ""}</span></span>)} />
      <Section title="Price changes" tone="warn" items={report.price_changed.map((u) => <span key={u.title}>{u.title} <span className="tnum text-muted">- {fmtEtb(u.old_price_etb)} to {fmtEtb(u.new_price_etb)} </span><Pill tone={u.change_pct < 0 ? "good" : "warn"}>{u.change_pct < 0 ? "Price drop" : "Price increase"} {u.change_pct > 0 ? "+" : ""}{fmtPct(u.change_pct)}</Pill></span>)} />
      <Section title="No longer on the page" tone="bad" items={report.removed.map((u) => u.title)} />
      <Section title="Back on the page" tone="good" items={report.reactivated.map((u) => u.title)} />
      <Section title="Skipped" tone="neutral" items={report.skipped.map((s) => `${s.title ?? "A unit"}: ${s.reason}`)} />
      <Section title="Could not read" tone="bad" items={report.parse_errors} />
      <Section title="Notes" tone="neutral" items={[...(report.floor_plan_for ? [`Floor plan attached to ${report.floor_plan_for}`] : []), ...report.notes, ...(report.sync.startsWith("skipped") ? [`Sync ${report.sync}`] : [])]} />
      {report.ads?.length > 0 && (
        <Section title="Channel ads" tone="good" items={report.ads.map((a) => <span key={a.title + a.kind}>{a.title} <span className="text-muted">- {a.kind.toLowerCase().replace("_", " ")}: {a.posted ? "posted" : a.error ?? a.reason ?? "not posted"}</span></span>)} />
      )}
    </div>
  );
}

export default function IngestPanel({ onChanged }: { onChanged: () => void }) {
  const developers = useApi<Developer[]>("developers");
  const [developer, setDeveloper] = useState("ayat");
  const [source, setSource] = useState<Source>("html");
  const [content, setContent] = useState("");
  const [profileText, setProfileText] = useState("");
  const [preview, setPreview] = useState(true);
  const [fullSync, setFullSync] = useState(false);
  const [postAds, setPostAds] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<IngestReport | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const planInput = useRef<HTMLInputElement>(null);

  async function choosePlan(file: File | undefined) {
    setError(null);
    if (!file) return;
    if (!PLAN_TYPES.includes(file.type)) { setError("The floor plan must be a PNG, JPEG or WebP image."); return; }
    if (file.size > PLAN_MAX_BYTES) { setError("The floor plan is larger than 5 MB."); return; }
    try { setPlan(await readPlan(file)); } catch (e) { setError(errorMessage(e)); }
  }

  function clearPlan() { setPlan(null); if (planInput.current) planInput.current.value = ""; }

  async function loadSample(variant: 1 | 2) {
    setError(null);
    try {
      const sample = await api<Sample>(`samples/listing?variant=${variant}`);
      setSource("html");
      setDeveloper(sample.developer_slug);
      setContent(sample.html);
      setProfileText(JSON.stringify(sample.profile, null, 2));
      setFullSync(true);
      setReport(null);
    } catch (e) { setError(errorMessage(e)); }
  }

  async function run() {
    setError(null);
    let profile: unknown;
    if (source === "html" && profileText.trim()) {
      try { profile = JSON.parse(profileText); } catch { setError("The selectors are not valid JSON."); return; }
    }
    setBusy(true);
    try {
      const result = await post<IngestReport>("ingest", {
        developer_slug: developer, source, html: source === "html" ? content : undefined, text: source === "text" ? content : undefined,
        profile, full_sync: source === "html" && fullSync, dry_run: preview, publish_ads: !preview && postAds,
        floor_plan_base64: source === "text" ? plan?.base64 : undefined, floor_plan_mime: source === "text" && plan ? plan.mime : undefined,
      });
      setReport(result);
      if (!preview) onChanged();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Card title="Read a listing page or announcement" action={<Pill tone="accent">Scraper</Pill>}>
      <div className="space-y-4 p-4">
        <p className="text-sm text-muted">
          Paste a developer&rsquo;s listing page or a price announcement. Units already known cost nothing; only new units are read by the model and added to search. Always preview first.
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted">Try a sample</span>
          <Button onClick={() => void loadSample(1)}>Ayat page, as it is now</Button>
          <Button onClick={() => void loadSample(2)}>Ayat page, a week later</Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <div>
            <label htmlFor="ingest-developer" className="block text-sm font-medium">Developer</label>
            <div className="mt-1"><Select id="ingest-developer" value={developer} onChange={setDeveloper} options={(developers.data ?? [{ slug: "ayat", name: "Ayat Real Estate" } as Developer]).map((d) => ({ value: d.slug, label: d.name }))} /></div>
          </div>
          <div role="tablist" aria-label="What to read" className="flex items-end gap-1">
            {([["html", "Listing page (HTML)"], ["text", "Announcement text"]] as const).map(([id, label]) => (
              <button key={id} role="tab" aria-selected={source === id} onClick={() => { setSource(id); setReport(null); }}
                className={clsx("rounded-t-md border border-b-0 px-3 py-2 text-sm font-medium", source === id ? "border-line bg-raised" : "border-transparent text-muted hover:text-ink")}>{label}</button>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="ingest-content" className="sr-only">{source === "html" ? "Page HTML" : "Announcement text"}</label>
          <textarea id="ingest-content" value={content} onChange={(e) => setContent(e.target.value)} rows={source === "html" ? 8 : 6} spellCheck={false}
            maxLength={source === "html" ? 500_000 : 20_000}
            placeholder={source === "html" ? "Paste the page's HTML here, or load a sample above" : "Paste the announcement, for example: Ayat announces Lake View, 3BR, 130 sqm, ETB 11.9M, 25% down..."}
            className={clsx("w-full rounded-md border border-line bg-bg px-3 py-2 text-xs outline-none focus:border-accent", source === "html" && "font-mono")} />
        </div>

        {source === "text" && (
          <div className="rounded-md border border-line px-3 py-2 text-sm">
            <label htmlFor="ingest-plan" className="font-medium">Floor plan image (optional)</label>
            <p className="mt-1 text-xs text-muted">Attached to the unit when the announcement describes exactly one new unit. The model lists its features so buyers can search by them.</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input id="ingest-plan" ref={planInput} type="file" accept={PLAN_TYPES.join(",")} onChange={(e) => void choosePlan(e.target.files?.[0])} className="text-xs" />
              {plan && <><span className="text-xs text-muted">{plan.name}</span><Button onClick={clearPlan}>Remove</Button></>}
            </div>
          </div>
        )}

        {source === "html" && (
          <details className="rounded-md border border-line px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Selectors {profileText.trim() ? "(set)" : "(optional)"}</summary>
            <p className="mt-2 text-xs text-muted">Tell the scraper where each field lives (CSS selectors). Leave empty and the language model reads the page instead, which is slower and uses quota.</p>
            <label htmlFor="ingest-profile" className="sr-only">Selectors as JSON</label>
            <textarea id="ingest-profile" value={profileText} onChange={(e) => setProfileText(e.target.value)} rows={6} spellCheck={false}
              className="mt-2 w-full rounded-md border border-line bg-bg px-3 py-2 font-mono text-xs" />
          </details>
        )}

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />Preview only</label>
          {source === "html" && (
            <label className="flex items-center gap-2" title="Units missing from this page are marked unavailable">
              <input type="checkbox" checked={fullSync} onChange={(e) => setFullSync(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />This is the full list (retire units not on it)
            </label>
          )}
          <label className={clsx("flex items-center gap-2", preview && "opacity-50")} title="Posts to your public Telegram channel">
            <input type="checkbox" checked={postAds && !preview} disabled={preview} onChange={(e) => setPostAds(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />Post new units and price cuts to the channel
          </label>
          <Button variant="primary" className="ml-auto" disabled={busy || !content.trim()} onClick={() => void run()}>{busy ? "Reading..." : preview ? "Preview changes" : "Apply changes"}</Button>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}
        {report && <ReportView report={report} />}
      </div>
    </Card>
  );
}
