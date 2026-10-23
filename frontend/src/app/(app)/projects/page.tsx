"use client";

import { useEffect, useState } from "react";
import Select from "@/components/Select";
import { Button, Card, Empty, ErrorNote, PageHeader, Pill, Stat, StatusDot } from "@/components/ui";
import { errorMessage, post, put } from "@/lib/api";
import { fmtDate, fmtEtb, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { AdResult, CrawlReport, Developer, PriceRequest, Project } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUS: Record<Project["status"], { tone: "good" | "warn" | "neutral"; label: string }> = {
  SELLING: { tone: "good", label: "Selling now" }, UPCOMING: { tone: "warn", label: "Coming soon" }, SOLD_OUT: { tone: "neutral", label: "Sold out" },
};

const range = (a: number | null, b: number | null, unit: string) => (a == null && b == null ? null : a != null && b != null && a !== b ? `${a}-${b} ${unit}` : `${a ?? b} ${unit}`);
const liveOffer = (p: Project) => !!p.promo_text && (!p.promo_until || p.promo_until >= new Date().toISOString().slice(0, 10));

type Dialog = { kind: "price" | "offer" | "ad"; project: Project } | null;

export default function ProjectsPage() {
  const { dataVersion } = useLive();
  const projects = useApi<Project[]>("projects", 30_000);
  const requests = useApi<PriceRequest[]>("price-requests", 30_000);
  const [dialog, setDialog] = useState<Dialog>(null);
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) { void projects.reload(); void requests.reload(); } }, [dataVersion]);

  const list = projects.data ?? [];
  const open = list.reduce((n, p) => n + Number(p.open_requests), 0);
  const reload = () => { void projects.reload(); void requests.reload(); };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projects"
        description="Developers that do not publish unit prices. A buyer who likes a project taps Ask the price in the bot; you ask the developer, enter the price here, and everyone waiting is told."
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Projects" icon="listing" value={list.length} hint={`${list.filter((p) => p.status === "SELLING").length} selling now`} />
        <Stat label="Live offers" icon="bell" value={list.filter(liveOffer).length} hint="Advertised in the channel once each" />
        <Stat label="Waiting for a price" icon="chat" value={open} hint="Open buyer requests" />
        <Stat label="Priced" icon="money" value={list.filter((p) => p.price_from_etb != null).length} hint="Price entered by you" />
      </div>

      <CrawlCard onDone={reload} />

      <Card title="All projects" subtitle="Read from the developers' own sites, or the demo projects">
        {projects.loading ? <Empty>Loading...</Empty> : list.length === 0 ? (
          <Empty>No projects yet. Read a developer&apos;s website above, or run the demo seed.</Empty>
        ) : (
          <ul>
            {list.map((p) => (
              <li key={p.id} className="border-t border-[#222] px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-base font-semibold">{p.name}</h3>
                      <StatusDot tone={STATUS[p.status].tone}>{STATUS[p.status].label}</StatusDot>
                      {liveOffer(p) && <Pill tone="accent">Offer{p.promo_discount_pct ? ` ${p.promo_discount_pct}% off` : ""}</Pill>}
                      {Number(p.open_requests) > 0 && <Pill tone="warn">{p.open_requests} waiting for a price</Pill>}
                    </div>
                    <p className="mt-0.5 text-sm text-muted">
                      {p.developer_name}{p.sub_city ? ` - ${p.sub_city}` : ""}
                      {[range(p.sqm_min, p.sqm_max, "sqm"), range(p.bedrooms_min, p.bedrooms_max, "bedrooms"), p.floors ? `${p.floors} floors` : null].filter(Boolean).map((x) => ` - ${x}`).join("")}
                    </p>
                    {p.location_text && <p className="mt-1 text-sm text-muted">{p.location_text}</p>}
                    {liveOffer(p) && <p className="mt-1 text-sm text-accent">{p.promo_text}{p.promo_until ? ` (until ${fmtDate(p.promo_until)})` : ""}</p>}
                    {!p.source_url.startsWith("demo:") && <a href={p.source_url} target="_blank" rel="noreferrer" className="mt-1 inline-block break-all text-xs text-muted hover:text-white">{p.source_url}</a>}
                  </div>
                  <div className="text-right">
                    <p className="tnum text-lg font-semibold">{p.price_from_etb != null ? `from ${fmtEtb(Number(p.price_from_etb))}` : <span className="text-muted">Price on request</span>}</p>
                    {p.price_note && <p className="text-xs text-muted">{p.price_note}</p>}
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant={Number(p.open_requests) > 0 ? "primary" : "default"} onClick={() => setDialog({ kind: "price", project: p })}>{p.price_from_etb != null ? "Change price" : "Set price"}</Button>
                  <Button onClick={() => setDialog({ kind: "offer", project: p })}>{p.promo_text ? "Change offer" : "Add offer"}</Button>
                  <Button variant="glow" disabled={p.status === "SOLD_OUT"} onClick={() => setDialog({ kind: "ad", project: p })}>Advertise in channel</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Price requests" subtitle="Buyers who asked the bot for a price">
        {requests.loading ? <Empty>Loading...</Empty> : (requests.data ?? []).length === 0 ? <Empty>No price requests yet.</Empty> : (
          <ul>
            {(requests.data ?? []).slice(0, 15).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-[#222] px-5 py-3 text-sm">
                <span><span className="font-medium">{r.buyer_name ?? "A buyer"}</span> <span className="text-muted">asked about</span> {r.project_name}</span>
                <span className="flex items-center gap-2 text-xs text-muted">{timeAgo(r.created_at)}<Pill tone={r.status === "OPEN" ? "warn" : "good"}>{r.status === "OPEN" ? "Waiting" : "Answered"}</Pill></span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {dialog?.kind === "price" && <PriceDialog project={dialog.project} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {dialog?.kind === "offer" && <OfferDialog project={dialog.project} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {dialog?.kind === "ad" && <AdDialog project={dialog.project} onClose={() => setDialog(null)} />}
    </div>
  );
}

function CrawlCard({ onDone }: { onDone: () => void }) {
  const developers = useApi<Developer[]>("developers");
  const [slug, setSlug] = useState("");
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<CrawlReport | null>(null);
  const chosen = slug || developers.data?.[0]?.slug || "";

  async function run() {
    setBusy(true); setError(null); setReport(null);
    try {
      const r = await post<CrawlReport>(`developers/${chosen}/crawl-projects`, { url: url.trim() || null, dry_run: preview });
      setReport(r);
      if (!preview) onDone();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Card title="Read a developer's website" subtitle="Reads up to 40 pages of one site (robots.txt respected, one page at a time) and keeps what looks like a single project: area, sizes, floors, status and any offer. It never collects prices. A full read can take a few minutes.">
      <div className="space-y-4 px-5 pb-5">
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <div>
            <label htmlFor="crawl-dev" className="block text-sm font-medium">Developer</label>
            <div className="mt-1"><Select id="crawl-dev" value={chosen} onChange={setSlug} options={(developers.data ?? []).map((d) => ({ value: d.slug, label: d.name }))} /></div>
          </div>
          <div>
            <label htmlFor="crawl-url" className="block text-sm font-medium">Website address</label>
            <input id="crawl-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://developer.example/" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />Preview only</label>
          <Button variant="primary" disabled={busy || !chosen || !url.trim()} onClick={() => void run()}>{busy ? "Reading the site..." : preview ? "Preview what it finds" : "Read and save"}</Button>
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
        {report && (
          <div className="rounded-lg border border-line bg-raised p-4 text-sm" role="status">
            <p className="font-medium">{report.dry_run ? "Preview" : "Saved"}: {report.pages_read} pages read, {report.created.length} new, {report.updated.length} changed, {report.unchanged.length} unchanged.</p>
            {[...report.created.map((p) => ({ ...p, tag: "New" })), ...report.updated.map((p) => ({ ...p, tag: "Changed" }))].map((p) => (
              <p key={p.source_url} className="mt-1.5"><Pill tone={p.tag === "New" ? "good" : "warn"}>{p.tag}</Pill> {p.name} <span className="text-muted">- {p.area ?? "area unknown"}, {p.status.toLowerCase().replace("_", " ")}{p.offer ? `, offer: ${p.offer}` : ""}</span></p>
            ))}
            {report.notes.map((n) => <p key={n} className="mt-1.5 text-xs text-muted">{n}</p>)}
          </div>
        )}
      </div>
    </Card>
  );
}

function Shell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-line bg-panel p-5 shadow-xl">
        <h2 className="text-base font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}

function PriceDialog({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: () => void }) {
  const [price, setPrice] = useState(project.price_from_etb != null ? String(Number(project.price_from_etb)) : "");
  const [note, setNote] = useState(project.price_note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [told, setTold] = useState<number | null>(null);

  async function save() {
    setBusy(true); setError(null);
    try {
      const r = await put<{ buyers_told: number }>(`projects/${project.id}/price`, { price_from_etb: Number(price), note: note.trim() || null });
      setTold(r.buyers_told);
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Shell title={`Price for ${project.name}`} onClose={told === null ? onClose : onSaved}>
      {told === null ? (
        <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <p className="mt-1 text-sm text-muted">{Number(project.open_requests) > 0 ? `${project.open_requests} buyer${Number(project.open_requests) === 1 ? " is" : "s are"} waiting. They are told as soon as you save.` : "No one is waiting yet; the price will be given to anyone who asks."}</p>
          <label htmlFor="project-price" className="mt-4 block text-sm font-medium">Starting price (ETB)</label>
          <input id="project-price" type="number" min="1" step="any" autoFocus required value={price} onChange={(e) => setPrice(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
          <label htmlFor="project-note" className="mt-3 block text-sm font-medium">Note (optional)</label>
          <input id="project-note" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="for example: from the smallest 2 bedroom" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
          {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
          <div className="mt-5 flex justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={busy || !(Number(price) > 0)}>{busy ? "Saving..." : "Save and tell buyers"}</Button></div>
        </form>
      ) : (
        <div>
          <p role="status" className="mt-3 text-sm">Saved. {told} buyer{told === 1 ? "" : "s"} told.</p>
          <div className="mt-5 flex justify-end"><Button variant="primary" onClick={onSaved}>Done</Button></div>
        </div>
      )}
    </Shell>
  );
}

function OfferDialog({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState(project.promo_text ?? "");
  const [pct, setPct] = useState(project.promo_discount_pct != null ? String(Number(project.promo_discount_pct)) : "");
  const [until, setUntil] = useState(project.promo_until ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(clear: boolean) {
    setBusy(true); setError(null);
    try {
      await put(`projects/${project.id}/promo`, clear ? { text: null } : { text: text.trim(), discount_pct: pct ? Number(pct) : null, until: until || null });
      onSaved();
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }

  return (
    <Shell title={`Offer for ${project.name}`} onClose={onClose}>
      <form onSubmit={(e) => { e.preventDefault(); void save(false); }}>
        <p className="mt-1 text-sm text-muted">Use the developer&apos;s own words. A live offer is advertised in the channel once, and again if you change it.</p>
        <label htmlFor="offer-text" className="mt-4 block text-sm font-medium">Offer</label>
        <textarea id="offer-text" autoFocus rows={3} maxLength={300} value={text} onChange={(e) => setText(e.target.value)} placeholder="for example: 5% off for reservations before the end of the month" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div><label htmlFor="offer-pct" className="block text-sm font-medium">Discount % (optional)</label><input id="offer-pct" type="number" min="0.5" max="100" step="0.5" value={pct} onChange={(e) => setPct(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" /></div>
          <div><label htmlFor="offer-until" className="block text-sm font-medium">Until (optional)</label><input id="offer-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" /></div>
        </div>
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <div className="mt-5 flex flex-wrap justify-between gap-2">
          <div>{project.promo_text && <Button type="button" variant="danger" disabled={busy} onClick={() => void save(true)}>Remove offer</Button>}</div>
          <div className="flex gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={busy || !text.trim()}>Save offer</Button></div>
        </div>
      </form>
    </Shell>
  );
}

function AdDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const [ad, setAd] = useState<AdResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function call(dryRun: boolean) {
    setBusy(true); setError(null);
    try { setAd(await post<AdResult>(`projects/${project.id}/ad`, { dry_run: dryRun })); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  useEffect(() => { void call(true); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Shell title={`Advertise ${project.name}`} onClose={onClose}>
      {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
      {ad?.posted && <p role="status" className="mt-3 text-sm text-good">Posted to the channel.</p>}
      {ad?.reason && <p role="status" className="mt-3 text-sm text-muted">{ad.reason}</p>}
      {ad?.preview && (
        <div className="mt-3 rounded-lg border border-line bg-raised p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Preview {ad.photo_url ? "(posted with its picture)" : ""}</p>
          {ad.photo_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={ad.photo_url} alt="The picture that will be posted with the ad" className="mb-3 max-h-56 rounded-lg border border-line object-contain" />
          )}
          <p className="whitespace-pre-wrap break-words text-sm">{ad.preview}</p>
          <div className="mt-3 flex flex-wrap gap-2">{ad.buttons?.map((b) => <span key={b} className="rounded-md border border-accent/40 bg-accent/10 px-3 py-1 text-xs font-medium text-accent">{b}</span>)}</div>
        </div>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <Button type="button" onClick={onClose}>{ad?.posted ? "Close" : "Cancel"}</Button>
        {!ad?.posted && <Button variant="primary" disabled={busy || !ad?.preview} onClick={() => void call(false)}>{busy ? "Posting..." : "Post to the channel"}</Button>}
      </div>
    </Shell>
  );
}
