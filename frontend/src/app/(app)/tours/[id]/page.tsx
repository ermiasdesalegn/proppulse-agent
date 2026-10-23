"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, Pill, ScoreBar, Stat } from "@/components/ui";
import { errorMessage, post } from "@/lib/api";
import { fmtDateTime, fmtEtb } from "@/lib/format";
import type { Dossier } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function DossierPage() {
  const { id } = useParams<{ id: string }>();
  const dossier = useApi<Dossier>(`tours/${id}/dossier`);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const d = dossier.data;

  async function send() {
    setBusy(true); setNote(null); setError(null);
    try {
      const res = await post<{ sent: string[] }>(`tours/${id}/dossier/send`, {});
      setNote(res.sent.length ? `Sent to the broker by ${res.sent.join(" and ")}.` : "Nowhere to send it: set the broker's Telegram chat or email in the backend settings.");
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  async function copy() {
    if (!d) return;
    try { await navigator.clipboard.writeText(d.text); setNote("Copied."); } catch { setError("Your browser would not let the page copy. Select the text and copy it."); }
  }

  if (dossier.loading) return <Empty>Loading the dossier...</Empty>;
  if (!d) return <div className="space-y-3"><ErrorNote>{dossier.error ?? "Dossier not found."}</ErrorNote><Link href="/map" className="text-sm text-accent hover:underline">Back to the map</Link></div>;

  const b = d.buyer;
  const call = d.kind === "CONSULTATION";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-accent">{call ? "Phone consultation" : "Site visit"} - pre-tour dossier</p>
          <h1 className="text-xl font-semibold">{b.name ?? "Unnamed buyer"}</h1>
          <p className="text-sm text-muted">{fmtDateTime(d.starts_at)}{d.route ? ` - ${d.route.distance_km} km, about ${d.route.minutes} min of driving` : ""}</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => void copy()}>Copy as text</Button>
          <Button variant="primary" disabled={busy} onClick={() => void send()}>Send to my Telegram</Button>
        </div>
      </div>
      {note && <p role="status" className="text-sm text-good">{note}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Expected commission" value={fmtEtb(d.expected_commission_etb)} hint="if they buy every unit shown" />
        <Stat label="Value in play" value={fmtEtb(d.total_value_etb)} hint={`${d.units.length} unit${d.units.length === 1 ? "" : "s"}`} />
        <Stat label="Budget" value={b.max_budget_etb ? fmtEtb(b.max_budget_etb) : "Not stated"} hint={d.budget_gap_etb && d.budget_gap_etb > 0 ? `average unit is ${fmtEtb(d.budget_gap_etb)} above it` : undefined} />
        <div className="rounded-xl border border-line bg-panel px-4 py-3"><p className="text-xs font-medium text-muted">Lead score</p><div className="mt-2"><ScoreBar score={b.lead_score} /></div><p className="mt-1 text-xs text-muted">{b.deal_stage.replace(/_/g, " ").toLowerCase()}</p></div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-4">
          {!call && (
            <Card title="Units on this visit">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[40rem] text-left text-sm">
                  <thead className="border-b border-line text-xs text-muted"><tr><th className="px-4 py-2 font-medium">Unit</th><th className="px-2 py-2 font-medium">Price</th><th className="px-2 py-2 font-medium">Payment plan</th><th className="px-2 py-2 text-right font-medium">Commission</th><th className="px-4 py-2 text-right font-medium">Developer reliability</th></tr></thead>
                  <tbody>
                    {d.units.map((u) => (
                      <tr key={u.property_id} className="border-b border-line/60 align-top">
                        <td className="px-4 py-2"><p className="font-medium">{u.title}</p><p className="text-xs text-muted">{u.developer} - {u.area}</p></td>
                        <td className="tnum px-2 py-2">{fmtEtb(u.price_etb)}<p className="text-xs text-muted">{u.price_per_sqm_etb.toLocaleString("en-US")} ETB/sqm</p></td>
                        <td className="px-2 py-2 text-xs">{u.payment_plan}<p className="text-muted">about {fmtEtb(u.monthly_equivalent_etb)} a month</p></td>
                        <td className="tnum px-2 py-2 text-right">{fmtEtb(u.expected_commission_etb)}<p className="text-xs text-muted">{u.commission_rate_pct}%</p></td>
                        <td className="tnum px-4 py-2 text-right">{u.reliability_score !== null ? `${Math.round(u.reliability_score * 100)}%` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <Card title="What they said lately">
            {d.recent_buyer_messages.length === 0 ? <Empty>No messages.</Empty> : (
              <ul className="divide-y divide-line/60">{d.recent_buyer_messages.map((m, i) => <li key={i} className="px-4 py-2.5 text-sm">&ldquo;{m}&rdquo;</li>)}</ul>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Worries to address">
            {d.objections.length === 0 ? <p className="px-4 py-4 text-sm text-muted">None raised yet.</p> : <ul className="flex flex-wrap gap-1.5 px-4 py-4">{d.objections.map((o) => <li key={o}><Pill tone="warn">{o}</Pill></li>)}</ul>}
          </Card>
          <Card title="Contact">
            <dl className="space-y-2 px-4 py-4 text-sm">
              <div><dt className="text-xs text-muted">Phone</dt><dd>{b.phone ?? "-"}</dd></div>
              <div><dt className="text-xs text-muted">Email</dt><dd className="break-all">{b.email ?? "-"}</dd></div>
              <div><dt className="text-xs text-muted">Wants</dt><dd>{b.wants ?? "-"}</dd></div>
              <div><dt className="text-xs text-muted">Areas</dt><dd>{b.areas.join(", ") || "-"}</dd></div>
            </dl>
            <p className="border-t border-line px-4 py-3 text-xs"><Link href="/crm" className="text-accent hover:underline">Open in CRM</Link></p>
          </Card>
        </div>
      </div>
    </div>
  );
}
