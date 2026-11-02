"use client";

import { useEffect, useState } from "react";
import Select from "@/components/Select";
import { Button, Card, Empty, ErrorNote, PageHeader, Pill, Stat, StatusDot } from "@/components/ui";
import { api, errorMessage, post, put } from "@/lib/api";
import { fmtDate, fmtEtb, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useMarketAccess } from "@/lib/useMarketAccess";
import type { AdResult, CrawlReport, Developer, PriceRequest, Project, SweepStatus } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUS: Record<Project["status"], { tone: "good" | "warn" | "neutral"; label: string }> = {
  SELLING: { tone: "good", label: "Selling now" }, UPCOMING: { tone: "warn", label: "Coming soon" }, SOLD_OUT: { tone: "neutral", label: "Sold out" },
};

const range = (a: number | null, b: number | null, unit: string) => (a == null && b == null ? null : a != null && b != null && a !== b ? `${a}-${b} ${unit}` : `${a ?? b} ${unit}`);
const liveOffer = (p: Project) => !!p.promo_text && (!p.promo_until || p.promo_until >= new Date().toISOString().slice(0, 10));

type Dialog = { kind: "price" | "offer" | "ad" | "media"; project: Project } | null;

export default function ProjectsPage() {
  const { dataVersion } = useLive();
  const canEdit = useMarketAccess(); // prices, offers, pictures and crawls change the shared catalogue, so only the operator's console shows them
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

      {canEdit ? (
        <>
          <SweepCard onDone={reload} />

          <CrawlCard onDone={reload} />
        </>
      ) : (
        <p role="note" className="rounded-lg border border-[#222] px-4 py-3 text-sm text-muted">The project catalogue is shared by every agency and is kept up to date by the platform. You can advertise any project in your own channel.</p>
      )}

      <Card title="All projects" subtitle="Read from the developers' own sites, or the demo projects">
        {projects.loading ? <Empty>Loading...</Empty> : list.length === 0 ? (
          <Empty>No projects yet. Read a developer&apos;s website above, or run the demo seed.</Empty>
        ) : (
          <ul>
            {list.map((p) => (
              <li key={p.id} className="border-t border-[#222] px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <Cover project={p} />
                  <div className="min-w-0 flex-1">
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
                  {canEdit && <Button variant={Number(p.open_requests) > 0 ? "primary" : "default"} onClick={() => setDialog({ kind: "price", project: p })}>{p.price_from_etb != null ? "Change price" : "Set price"}</Button>}
                  {canEdit && <Button onClick={() => setDialog({ kind: "offer", project: p })}>{p.promo_text ? "Change offer" : "Add offer"}</Button>}
                  {canEdit && <Button onClick={() => setDialog({ kind: "media", project: p })}>{p.cover_media_id || p.image_url ? "Photo or video" : "Add photo or video"}</Button>}
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
      {dialog?.kind === "media" && <MediaDialog project={dialog.project} onClose={() => setDialog(null)} onChanged={reload} />}
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
  const [adding, setAdding] = useState(false);
  const chosenDeveloper = developers.data?.find((d) => d.slug === chosen);

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
            <div className="mt-1"><Select id="crawl-dev" value={chosen} onChange={(v) => { setSlug(v); const d = developers.data?.find((x) => x.slug === v); if (d?.website_url && !url) setUrl(d.website_url); }} options={(developers.data ?? []).map((d) => ({ value: d.slug, label: d.name }))} /></div>
            <button type="button" onClick={() => setAdding(true)} className="mt-1.5 text-xs font-medium text-accent hover:underline">+ Add a developer</button>
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
        {adding && <DeveloperDialog onClose={() => setAdding(false)} onCreated={(d) => { setAdding(false); void developers.reload(); setSlug(d.slug); if (d.website_url) setUrl(d.website_url); }} />}
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
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Preview {ad.media ? (ad.media.kind === "video" ? "(posted with its video)" : "(posted with its picture)") : ""}</p>
          {ad.media?.kind === "video" && <video src={`/api/proxy/${ad.media.url}`} controls muted preload="metadata" className="mb-3 max-h-56 rounded-lg border border-line" />}
          {ad.media?.kind === "photo" && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={ad.media.url.startsWith("http") ? ad.media.url : `/api/proxy/${ad.media.url}`} alt="The picture that will be posted with the ad" className="mb-3 max-h-56 rounded-lg border border-line object-contain" />
          )}
          {ad.warning && <p className="mb-2 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">{ad.warning}</p>}
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

function DeveloperDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (d: { slug: string; website_url: string | null }) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [site, setSite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true); setError(null);
    try { onCreated(await post<{ slug: string; website_url: string | null }>("developers", { name: name.trim(), sales_email: email.trim(), website_url: site.trim() || null })); }
    catch (e) { setError(errorMessage(e)); setBusy(false); }
  }

  return (
    <Shell title="Add a developer" onClose={onClose}>
      <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <p className="mt-1 text-sm text-muted">The sales email receives the lead-registration notice when a buyer books a visit. Use a real address before booking real visits.</p>
        <label htmlFor="dev-name" className="mt-4 block text-sm font-medium">Company name</label>
        <input id="dev-name" autoFocus required minLength={2} value={name} onChange={(e) => setName(e.target.value)} placeholder="Metropolitan Real Estate" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
        <label htmlFor="dev-email" className="mt-3 block text-sm font-medium">Sales office email</label>
        <input id="dev-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="sales@developer.example" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
        <label htmlFor="dev-site" className="mt-3 block text-sm font-medium">Website (optional)</label>
        <input id="dev-site" type="url" value={site} onChange={(e) => setSite(e.target.value)} placeholder="https://developer.example/" className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <div className="mt-5 flex justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={busy || name.trim().length < 2 || !email.includes("@")}>{busy ? "Adding..." : "Add developer"}</Button></div>
      </form>
    </Shell>
  );
}

function Cover({ project: p }: { project: Project }) {
  const box = "h-20 w-28 shrink-0 overflow-hidden rounded-lg border border-line bg-raised object-cover";
  if (p.cover_media_id && p.cover_media_kind === "video") return <video src={`/api/proxy/projects/${p.id}/media/${p.cover_media_id}`} muted preload="metadata" className={box} />;
  const src = p.cover_media_id ? `/api/proxy/projects/${p.id}/media/${p.cover_media_id}` : p.image_url;
  // eslint-disable-next-line @next/next/no-img-element
  if (src) return <img src={src} alt="" className={box} />;
  return <div className={`${box} grid place-items-center px-2 text-center text-[11px] leading-tight text-muted`}>No photo or video yet</div>;
}

function MediaDialog({ project, onClose, onChanged }: { project: Project; onClose: () => void; onChanged: () => void }) {
  const media = useApi<{ id: number; kind: "photo" | "video"; filename: string | null; size_bytes: number }[]>(`projects/${project.id}/media`);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      await post(`projects/${project.id}/media`, { filename: file.name, mime: file.type, data_base64: btoa(bin) });
      await media.reload(); onChanged();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  async function remove(id: number) {
    setBusy(true); setError(null);
    try { await api(`projects/${project.id}/media/${id}`, { method: "DELETE" }); await media.reload(); onChanged(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  async function saveUrl() {
    setBusy(true); setError(null);
    try { await put(`projects/${project.id}/image`, { url: url.trim() || null }); onChanged(); setUrl(""); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Shell title={`Photo or video for ${project.name}`} onClose={onClose}>
      <p className="mt-1 text-sm text-muted">The newest upload is posted with the channel ad. Without an upload, the picture found on the developer&apos;s page is used.{project.image_url ? " A picture was found there." : " None was found there."}</p>
      <label htmlFor="media-file" className="mt-4 block text-sm font-medium">Upload (JPG, PNG, WebP up to 8 MB; MP4, MOV, WebM up to 20 MB)</label>
      <input id="media-file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm" disabled={busy} onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} className="mt-1 block w-full text-sm" />
      {(media.data ?? []).length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm">
          {(media.data ?? []).map((m) => (
            <li key={m.id} className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-1.5">
              <span className="min-w-0 truncate">{m.kind === "video" ? "Video" : "Photo"} - {m.filename ?? "upload"} <span className="text-muted">({Math.round(m.size_bytes / 1024)} KB)</span></span>
              <button onClick={() => void remove(m.id)} disabled={busy} className="shrink-0 text-xs text-bad hover:underline">Remove</button>
            </li>
          ))}
        </ul>
      )}
      <label htmlFor="media-url" className="mt-4 block text-sm font-medium">Or a picture address</label>
      <div className="mt-1 flex gap-2">
        <input id="media-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://developer.example/photo.jpg" className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
        <Button type="button" disabled={busy || !url.trim()} onClick={() => void saveUrl()}>Use</Button>
      </div>
      {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
      <div className="mt-5 flex justify-end"><Button variant="primary" onClick={onClose}>Done</Button></div>
    </Shell>
  );
}

function SweepCard({ onDone }: { onDone: () => void }) {
  const status = useApi<SweepStatus>("market-sweep", 5_000);
  const [save, setSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const running = !!status.data?.sweep.running;
  const last = status.data?.sweep.last;
  useEffect(() => { if (!running && last) onDone(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  async function start() {
    setBusy(true); setError(null); setNote(null);
    try {
      const r = await post<{ jobs: { details: { note?: string }[] }[] }>(`cron/scrape-market?dry_run=${!save}`);
      setNote(r.jobs[0]?.details.map((d) => d.note).filter(Boolean).join(" ") ?? "Started.");
      await status.reload();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Card title="Market sweep" subtitle="Reads these developer sites with the language model, no selectors to maintain. It runs in the background and can take several minutes.">
      <div className="space-y-4 px-5 pb-5">
        <ul className="space-y-1 text-sm">
          {(status.data?.sources ?? []).map((s) => <li key={s.url} className="flex flex-wrap gap-x-2"><span className="font-medium">{s.developer}</span><span className="break-all text-muted">{s.url}</span></li>)}
        </ul>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />Save what it finds (otherwise preview only)</label>
          <Button variant="primary" disabled={busy || running} onClick={() => void start()}>{running ? "Sweep running..." : save ? "Run and save" : "Run a preview"}</Button>
        </div>
        {running && <p role="status" className="text-sm text-muted">Reading {status.data?.sweep.current ?? "..."}</p>}
        {note && <p role="status" className="text-sm text-muted">{note}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
        {last && !running && (
          <div className="rounded-lg border border-line bg-raised p-4 text-sm" role="status">
            <p className="font-medium">Last sweep ({last.dry_run ? "preview" : "saved"}): {last.sent} new or changed, {last.skipped} unchanged, {last.failed} site{last.failed === 1 ? "" : "s"} failed.</p>
            {last.details.map((d, i) => (
              <p key={i} className="mt-1.5 break-words">
                <span className="font-medium">{d.developer ?? "sweep"}</span>{" "}
                {d.error ? <span className="text-bad">{d.error}</span> : <span className="text-muted">{d.pages_read} pages, {(d.new ?? []).length} new{(d.new ?? []).length ? ` (${(d.new ?? []).join(", ")})` : ""}, {(d.changed ?? []).length} changed, {d.unchanged ?? 0} unchanged</span>}
              </p>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
