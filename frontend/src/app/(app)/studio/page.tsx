"use client";

import { useState } from "react";
import { Button, Card, ErrorNote, Pill } from "@/components/ui";
import { errorMessage, post } from "@/lib/api";
import { fmtEtb } from "@/lib/format";
import type { Property } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface PriceResult {
  property_id: string;
  old_price_etb: number;
  new_price_etb: number;
  change_pct: number;
  changed: boolean;
}

const COMING = [
  { title: "Market scraper", text: "Reads each partner developer's listing pages and spots new units and price changes automatically." },
  { title: "Floor-plan vision", text: "Turns floor-plan images into searchable features such as open kitchen, maid room or corner balcony." },
  { title: "Telegram channel ads", text: "Writes and publishes a listing post with buttons for questions, comparison and booking a visit." },
];

export default function StudioPage() {
  const properties = useApi<Property[]>("properties");
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
      <div>
        <h1 className="text-xl font-semibold">Studio</h1>
        <p className="text-sm text-muted">Simulate what developers do, then watch the agent react.</p>
      </div>

      <Card title="Price change simulator">
        <div className="space-y-4 p-4">
          <p className="text-sm text-muted">
            A developer lowers a price. Change one here, then press <strong>Trigger Reverse-RAG Price-Drop Alerts</strong> in the top bar. Leave Preview only on to see which buyers would be told; turn it off to send the real message.
            This edits the real price in the database, so revert it afterwards.
          </p>
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
            <div>
              <label htmlFor="unit" className="block text-sm font-medium">Unit</label>
              <select id="unit" value={propertyId} onChange={(e) => { setPropertyId(e.target.value); setResult(null); setPrice(""); }}
                className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm">
                <option value="">Choose a unit...</option>
                {(properties.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.title} ({p.sub_city}) - {fmtEtb(Number(p.price_etb))}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="new-price" className="block text-sm font-medium">New price (ETB)</label>
              <input id="new-price" type="number" min="1" step="1000" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={selected ? String(Number(selected.price_etb)) : ""}
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

      <div>
        <h2 className="mb-3 text-sm font-semibold">Coming with the ingestion pipeline</h2>
        <div className="grid gap-3 md:grid-cols-3">
          {COMING.map((c) => (
            <div key={c.title} className="rounded-xl border border-dashed border-line bg-panel p-4">
              <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">{c.title}</h3><Pill>Not built yet</Pill></div>
              <p className="mt-2 text-sm text-muted">{c.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
