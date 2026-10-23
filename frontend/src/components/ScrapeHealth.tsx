"use client";

import { useState } from "react";
import Select from "@/components/Select";
import { Button, Card, Empty, ErrorNote, Pill } from "@/components/ui";
import { api, errorMessage, post } from "@/lib/api";
import { fmtEtb, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Health { slug: string; name: string; url: string; fetch_mode: "static" | "browser" | "auto"; status: "ok" | "warning" | "failing" | "layout_changed" | "never_run"; message: string; last_run_at: string | null; items_found: number | null }
interface Suggestion { profile: { item: string; fields: Record<string, string>; defaults: Record<string, string> }; reasoning: string; found: number; usable: boolean; errors: string[]; preview: { title: string; sub_city: string; sqm: number; price_etb: number }[] }

const TONE = { ok: "good", warn: "warn", warning: "warn", failing: "bad", layout_changed: "warn", never_run: "neutral" } as const;
const LABEL = { ok: "Healthy", warning: "Failing", failing: "Failing repeatedly", layout_changed: "Layout may have changed", never_run: "Not read yet" } as const;
const MODES = [["auto", "Automatic"], ["static", "Plain page"], ["browser", "Browser (JavaScript)"]] as const;

/** How each saved developer page is doing, how it is read, and a way to onboard a new page: the model proposes selectors, you review them. */
export default function ScrapeHealth() {
  const health = useApi<Health[]>("scrape/health", 60_000);
  const [onboardUrl, setOnboardUrl] = useState("");
  const [slug, setSlug] = useState("");
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function setMode(h: Health, mode: string) {
    setError(null);
    try {
      const dev = (await api<{ slug: string; scrape_url: string; scrape_profile: unknown }[]>("developers")).find((d) => d.slug === h.slug);
      await fetch(`/api/proxy/developers/${h.slug}/scrape`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ url: dev?.scrape_url, profile: dev?.scrape_profile, fetch_mode: mode }) });
      await health.reload();
    } catch (e) { setError(errorMessage(e)); }
  }

  async function suggest() {
    setBusy(true); setError(null); setSuggestion(null); setNote(null);
    try { setSuggestion(await post<Suggestion>(`developers/${slug}/suggest-profile`, { url: onboardUrl })); }
    catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  async function save() {
    if (!suggestion) return;
    setBusy(true); setError(null);
    try {
      const res = await fetch(`/api/proxy/developers/${slug}/scrape`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ url: onboardUrl, profile: suggestion.profile }) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Could not save");
      setNote("Saved. The scheduled scrape will now read this page."); setSuggestion(null); await health.reload();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <Card title="Scrape health" action={<span className="text-xs text-muted">pages read by the scheduled scraper</span>}>
      {error && <div className="px-4 pt-3"><ErrorNote>{error}</ErrorNote></div>}
      {health.loading ? <Empty>Loading...</Empty> : (health.data ?? []).length === 0 ? <Empty>No developer page is saved yet. Add one below.</Empty> : (
        <ul className="divide-y divide-line/60">
          {health.data!.map((h) => (
            <li key={h.slug} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
              <div className="min-w-0">
                <p className="flex items-center gap-2 font-medium">{h.name} <Pill tone={TONE[h.status]}>{LABEL[h.status]}</Pill></p>
                <p className="text-xs text-muted">{h.message}{h.last_run_at ? ` Last read ${timeAgo(h.last_run_at)}.` : ""}</p>
                <p className="truncate text-xs text-muted">{h.url}</p>
              </div>
              <label className="text-xs text-muted">How it is read
                <span className="ml-2 inline-block w-48 align-middle"><Select size="sm" ariaLabel="How it is read" value={h.fetch_mode} onChange={(v) => void setMode(h, v)} options={MODES.map(([value, label]) => ({ value, label }))} /></span>
              </label>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-3 border-t border-line px-4 py-4">
        <p className="text-sm font-medium">Add a developer page</p>
        <p className="text-xs text-muted">Give the developer and the address of its listing page. The language model proposes where each field lives and tries it on the page; you see what it found before anything is saved.</p>
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="onboard-dev">Developer</label>
          <div className="w-44"><Select id="onboard-dev" value={slug} onChange={setSlug} placeholder="Developer..." options={[{ value: "ayat", label: "Ayat" }, { value: "noah", label: "Noah" }, { value: "gift", label: "Gift" }]} /></div>
          <label className="sr-only" htmlFor="onboard-url">Page address</label>
          <input id="onboard-url" value={onboardUrl} onChange={(e) => setOnboardUrl(e.target.value)} placeholder="https://developer.example/units" className="min-w-[16rem] flex-1 rounded-md border border-line bg-bg px-3 py-1.5 text-sm" />
          <Button variant="primary" disabled={busy || !slug || !onboardUrl.startsWith("http")} onClick={() => void suggest()}>{busy ? "Working..." : "Suggest selectors"}</Button>
        </div>
        {note && <p role="status" className="text-sm text-good">{note}</p>}
        {suggestion && (
          <div className="rounded-lg border border-line bg-raised p-3 text-sm">
            <p className="font-medium">{suggestion.usable ? `Found ${suggestion.found} unit${suggestion.found === 1 ? "" : "s"}.` : "These selectors did not find usable units."}</p>
            {suggestion.reasoning && <p className="mt-1 text-xs text-muted">{suggestion.reasoning}</p>}
            {suggestion.preview.length > 0 && <ul className="mt-2 space-y-1 text-xs">{suggestion.preview.map((u) => <li key={u.title}>{u.title} - {u.sub_city}, {u.sqm} sqm, {fmtEtb(u.price_etb)}</li>)}</ul>}
            {suggestion.errors.length > 0 && <p className="mt-2 text-xs text-warn">{suggestion.errors.join("; ")}</p>}
            <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted">Selectors</summary><pre className="mt-1 overflow-x-auto font-mono">{JSON.stringify(suggestion.profile, null, 2)}</pre></details>
            <div className="mt-3 flex gap-2"><Button variant="primary" disabled={busy || !suggestion.usable} onClick={() => void save()}>Save for the scheduled scrape</Button><Button onClick={() => setSuggestion(null)}>Discard</Button></div>
          </div>
        )}
      </div>
    </Card>
  );
}
