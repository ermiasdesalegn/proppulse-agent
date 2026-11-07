"use client";

import { useState } from "react";
import { Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useMarketAccess } from "@/lib/useMarketAccess";
import { useApi } from "@/lib/useApi";

interface Agency {
  id: string;
  slug: string;
  name: string;
  plan: "free" | "pro" | "business";
  status: "active" | "suspended";
  created_at: string;
  operator: boolean;
  members: number;
  buyers: number;
  messages_today: number;
}

const select = "rounded-md border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30 disabled:opacity-50";

function Row({ agency, onSaved }: { agency: Agency; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(body: { plan?: string; status?: string }) {
    setBusy(true);
    setError(null);
    try { await api(`operator/agencies/${agency.id}`, { method: "PATCH", body: JSON.stringify(body) }); onSaved(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <tr className="border-t border-line align-top">
      <td className="py-3 pr-4">
        <div className="font-medium">{agency.name} {agency.operator && <Pill tone="good">operator</Pill>}</div>
        <div className="text-xs text-muted">/{agency.slug} · since {fmtDate(agency.created_at)}</div>
      </td>
      <td className="py-3 pr-4">
        <select aria-label={`Plan of ${agency.name}`} className={select} disabled={busy} value={agency.plan} onChange={(e) => void change({ plan: e.target.value })}>
          <option value="free">free</option><option value="pro">pro</option><option value="business">business</option>
        </select>
      </td>
      <td className="py-3 pr-4">
        <select aria-label={`Status of ${agency.name}`} className={select} disabled={busy || agency.operator} value={agency.status} onChange={(e) => void change({ status: e.target.value })}>
          <option value="active">active</option><option value="suspended">suspended</option>
        </select>
      </td>
      <td className="py-3 pr-4 text-muted">{agency.members}</td>
      <td className="py-3 pr-4 text-muted">{agency.buyers}</td>
      <td className="py-3 text-muted">{agency.messages_today}{error && <p role="alert" className="text-xs text-bad">{error}</p>}</td>
    </tr>
  );
}

export default function OperatorPage() {
  const allowed = useMarketAccess(); // the operator's own console only; the backend refuses everyone else anyway
  const data = useApi<{ agencies: Agency[] }>(allowed ? "operator/agencies" : null, 30_000);
  const list = data.data?.agencies ?? [];
  return (
    <div className="space-y-6">
      <PageHeader
        title="Agencies"
        description="Every agency on the platform. Changing a plan changes its daily message cap, monthly model budget and daily job cap at once. A suspended agency's team, bot and public pages are shut off until you set it active again. Each change is written to that agency's history."
      />
      {!allowed && <Empty>This page is for the platform operator.</Empty>}
      {data.error && <ErrorNote>{data.error}</ErrorNote>}
      {allowed && (
        <Card>
          {data.loading && !data.data ? <Empty>Loading agencies...</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted">
                  <tr><th className="pb-2 pr-4">Agency</th><th className="pb-2 pr-4">Plan</th><th className="pb-2 pr-4">Status</th><th className="pb-2 pr-4">Members</th><th className="pb-2 pr-4">Buyers</th><th className="pb-2">Messages today</th></tr>
                </thead>
                <tbody>{list.map((a) => <Row key={`${a.id}:${a.plan}:${a.status}`} agency={a} onSaved={() => void data.reload()} />)}</tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
