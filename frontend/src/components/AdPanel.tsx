"use client";

import { useEffect, useState } from "react";
import { Button, Card, Empty, ErrorNote, Pill } from "./ui";
import { errorMessage, post } from "@/lib/api";
import { fmtEtb, timeAgo } from "@/lib/format";
import type { AdResult, ChannelPost, Property } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function AdPanel() {
  const properties = useApi<Property[]>("properties");
  const posts = useApi<ChannelPost[]>("ads?limit=8", 60_000);
  const [propertyId, setPropertyId] = useState("");
  const [ad, setAd] = useState<AdResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => { setAd(null); setConfirming(false); setDone(null); setError(null); }, [propertyId]);

  async function call(dryRun: boolean) {
    setBusy(true); setError(null); setDone(null);
    try {
      const result = await post<AdResult>("ads/publish", { property_id: propertyId, kind: "MANUAL", dry_run: dryRun });
      if (dryRun) setAd(result);
      else { setDone(result.posted ? "Posted to the channel." : result.reason ?? "Not posted."); setConfirming(false); await posts.reload(); }
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  const available = (properties.data ?? []).filter((p) => p.status === "AVAILABLE");
  return (
    <Card title="Telegram channel ad" action={<Pill tone="accent">Publisher</Pill>}>
      <div className="space-y-4 p-4">
        <p className="text-sm text-muted">Write an ad for one unit, preview it, then post it. Every number comes from the database; the buttons open a chat with the bot that already knows the unit.</p>
        <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <div>
            <label htmlFor="ad-unit" className="block text-sm font-medium">Unit</label>
            <select id="ad-unit" value={propertyId} onChange={(e) => setPropertyId(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm">
              <option value="">Choose a unit...</option>
              {available.map((p) => <option key={p.id} value={p.id}>{p.title} ({p.sub_city}) - {fmtEtb(Number(p.price_etb))}</option>)}
            </select>
          </div>
          <Button disabled={!propertyId || busy} onClick={() => void call(true)}>{busy && !confirming ? "Writing..." : "Preview ad"}</Button>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}
        {ad?.preview && (
          <div className="rounded-lg border border-line bg-raised p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Preview {ad.has_photo ? "(posted with the floor plan)" : ""}</p>
            <p className="whitespace-pre-wrap break-words text-sm">{ad.preview}</p>
            <div className="mt-3 flex flex-wrap gap-2">{ad.buttons?.map((b) => <span key={b} className="rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs font-medium text-accent">{b}</span>)}</div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {!confirming ? (
                <Button variant="primary" onClick={() => setConfirming(true)}>Post to the channel...</Button>
              ) : (
                <>
                  <span className="text-sm text-warn">This posts publicly to your Telegram channel and cannot be undone from here.</span>
                  <Button variant="danger" disabled={busy} onClick={() => void call(false)}>{busy ? "Posting..." : "Yes, post it"}</Button>
                  <Button onClick={() => setConfirming(false)}>Cancel</Button>
                </>
              )}
            </div>
          </div>
        )}
        {done && <p role="status" className="text-sm text-good">{done}</p>}

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Recent posts</h3>
          {(posts.data ?? []).length === 0 ? <Empty>Nothing posted yet.</Empty> : (
            <ul className="divide-y divide-line/60 rounded-lg border border-line">
              {posts.data!.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">{p.title} <span className="text-muted">- {fmtEtb(Number(p.price_etb))}</span></span>
                  <span className="flex shrink-0 items-center gap-2"><Pill>{p.kind.toLowerCase().replace("_", " ")}</Pill><span className="text-xs text-muted">{timeAgo(p.created_at)}</span></span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}
