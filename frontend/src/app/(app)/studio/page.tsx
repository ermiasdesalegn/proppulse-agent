"use client";

import { useState } from "react";
import AdPanel from "@/components/AdPanel";
import IngestPanel from "@/components/IngestPanel";
import ScrapeHealth from "@/components/ScrapeHealth";
import Select from "@/components/Select";
import { PageHeader, Button, Card, Empty, ErrorNote, Pill } from "@/components/ui";
import { errorMessage, post } from "@/lib/api";
import { fmtEtb, timeAgo } from "@/lib/format";
import type { IngestRun, Property } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface PriceResult {
  property_id: string;
  old_price_etb: number;
  new_price_etb: number;
  change_pct: number;
  changed: boolean;
}

export default function StudioPage() {
  const properties = useApi<Property[]>("properties");
  const runs = useApi<IngestRun[]>("ingest/runs?limit=8", 60_000);
  const [propertyId, setPropertyId] = useState("");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PriceResult | null>(null);

  const selected = (properties.data ?? []).find((p) => p.id === propertyId);

  async function apply(id: string, value: number) {
    setBusy(true); setError(null);
    try {
      setResult(await post<PriceResult>(`properties/${id}/price`, { price_etb: value }));
      await properties.reload();
    } catch (e) {
      setError(errorMessage(e));
    }
    setBusy(false);
  }

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Workbench" title="Studio" description={<>Bring in developer listings, advertise units, and simulate what developers do to watch the agent react.</>} />

      <IngestPanel onChanged={() => { void properties.reload(); void runs.reload(); }} />
      <ScrapeHealth />
      <AdPanel />

      <Card title="Price change simulator">
        <div className="space-y-4 p-4">
          <p className="text-sm text-muted">
            A developer lowers a price. Change one here, then press <strong>Trigger Reverse-RAG Price-Drop Alerts</strong> in the top bar. Leave Preview only on to see which buyers would be told; turn it off to send the real message.
            This edits the real price in the database, so revert it afterwards.
          </p>
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
            <div>
              <label htmlFor="unit" className="block text-sm font-medium">Unit</label>
              <div className="mt-1"><Select id="unit" value={propertyId} placeholder="Choose a unit..." onChange={(v) => { setPropertyId(v); setResult(null); setPrice(""); }}
                options={(properties.data ?? []).map((p) => ({ value: p.id, label: p.title, hint: `${p.sub_city} - ${fmtEtb(Number(p.price_etb))}` }))} /></div>
            </div>
            <div>
              <label htmlFor="new-price" className="block text-sm font-medium">New price (ETB)</label>
              <input id="new-price" type="number" min="1" step="any" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={selected ? String(Number(selected.price_etb)) : ""}
                className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm" />
            </div>
            <Button variant="primary" disabled={busy || !selected || !(Number(price) > 0)} onClick={() => selected && void apply(selected.id, Number(price))}>Apply price change</Button>
          </div>

          {error && <ErrorNote>{error}</ErrorNote>}
          {result && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-raised px-3 py-2 text-sm" role="status">
              {result.changed ? (
                <>
                  <Pill tone={result.change_pct < 0 ? "good" : "warn"}>{result.change_pct > 0 ? "+" : ""}{result.change_pct}%</Pill>
                  <span>{fmtEtb(result.old_price_etb)} to {fmtEtb(result.new_price_etb)}</span>
                  <Button disabled={busy} onClick={() => void apply(result.property_id, result.old_price_etb)}>Revert to {fmtEtb(result.old_price_etb)}</Button>
                </>
              ) : <span>That is already the price. Nothing changed.</span>}
            </div>
          )}
        </div>
      </Card>

      <Card title="Recent ingestion runs">
        {runs.loading ? <Empty>Loading...</Empty> : (runs.data ?? []).length === 0 ? <Empty>No runs yet. Applied changes are listed here.</Empty> : (
          <ul className="divide-y divide-line/60">
            {runs.data!.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <span>{r.developer_name ?? "Unknown developer"} <span className="text-muted">- {r.source}{r.full_sync ? ", full list" : ""}</span></span>
                <span className="tnum flex items-center gap-2 text-xs text-muted">
                  <Pill tone="good">{r.created} new</Pill><Pill tone="warn">{r.price_changed} price</Pill><Pill tone="bad">{r.removed} removed</Pill>{timeAgo(r.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
