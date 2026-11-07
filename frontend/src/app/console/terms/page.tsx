"use client";

import { useState } from "react";
import { Button, Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import { api, errorMessage, put } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { termBody } from "@/lib/terms";
import { useApi } from "@/lib/useApi";

interface Term {
  developer_id: string;
  name: string;
  slug: string;
  shared_rate_pct: number;
  agency_rate_pct: number | null;
  effective_rate_pct: number;
  note: string | null;
  valid_until: string | null;
}

const input = "w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";

function Row({ term, onSaved }: { term: Term; onSaved: () => void }) {
  const [rate, setRate] = useState(term.agency_rate_pct == null ? "" : String(term.agency_rate_pct));
  const [note, setNote] = useState(term.note ?? "");
  const [until, setUntil] = useState(term.valid_until ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const own = term.agency_rate_pct != null;

  async function save() {
    const checked = termBody(rate, note, until);
    if ("error" in checked) { setError(checked.error); return; }
    setBusy(true);
    setError(null);
    try { await put(`developer-terms/${term.developer_id}`, checked.body); onSaved(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  async function clear() {
    setBusy(true);
    setError(null);
    try { await api(`developer-terms/${term.developer_id}`, { method: "DELETE" }); setRate(""); setNote(""); setUntil(""); onSaved(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <tr className="border-t border-line align-top">
      <td className="py-3 pr-4">
        <div className="font-medium">{term.name}</div>
        {own ? <Pill tone="good">Your rate in force: {term.effective_rate_pct}%</Pill> : <span className="text-xs text-muted">Using the shared rate</span>}
      </td>
      <td className="py-3 pr-4 text-muted">{term.shared_rate_pct}%</td>
      <td className="w-28 py-3 pr-4"><input aria-label={`Your rate with ${term.name}`} inputMode="decimal" placeholder="%" value={rate} onChange={(e) => setRate(e.target.value)} className={input} /></td>
      <td className="py-3 pr-4"><input aria-label={`Note for ${term.name}`} maxLength={200} placeholder="Agreed on a date, with whom" value={note} onChange={(e) => setNote(e.target.value)} className={input} /></td>
      <td className="w-40 py-3 pr-4"><input aria-label={`Valid until for ${term.name}`} type="date" value={until} onChange={(e) => setUntil(e.target.value)} className={input} /></td>
      <td className="py-3">
        <div className="flex gap-2">
          <Button variant="primary" disabled={busy || rate.trim() === ""} onClick={() => void save()}>{busy ? "Saving..." : "Save"}</Button>
          {own && <Button disabled={busy} onClick={() => void clear()}>Use shared</Button>}
        </div>
        {error && <p role="alert" className="mt-1 text-xs text-bad">{error}</p>}
        {own && term.valid_until && <p className="mt-1 text-xs text-muted">Until {fmtDate(term.valid_until)}</p>}
      </td>
    </tr>
  );
}

export default function TermsPage() {
  const terms = useApi<{ developers: Term[] }>("developer-terms");
  const list = terms.data?.developers ?? [];
  return (
    <div className="space-y-6">
      <PageHeader
        title="Commission terms"
        description="The commission you agreed with each developer. Your own rate is used for your expected commissions, dossiers and analytics; other agencies never see it. A rate you do not set uses the shared one. Only admins can change a rate."
      />
      {terms.error && <ErrorNote>{terms.error}</ErrorNote>}
      <Card>
        {terms.loading && !terms.data ? <Empty>Loading developers...</Empty>
          : list.length === 0 ? <Empty>No developers yet.</Empty>
          : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted">
                  <tr><th className="pb-2 pr-4">Developer</th><th className="pb-2 pr-4">Shared rate</th><th className="pb-2 pr-4">Your rate</th><th className="pb-2 pr-4">Note</th><th className="pb-2 pr-4">Valid until</th><th className="pb-2" /></tr>
                </thead>
                <tbody>{list.map((t) => <Row key={`${t.developer_id}:${t.agency_rate_pct}:${t.note}:${t.valid_until}`} term={t} onSaved={() => void terms.reload()} />)}</tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}
