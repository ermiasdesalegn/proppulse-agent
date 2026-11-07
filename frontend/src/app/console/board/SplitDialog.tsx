"use client";

import { useEffect, useState } from "react";
import Select from "@/components/Select";
import { Button, ErrorNote } from "@/components/ui";
import { errorMessage, put } from "@/lib/api";
import { memberLabel, splitBody } from "@/lib/assignment";
import { fmtEtb } from "@/lib/format";
import type { Deal } from "@/lib/types";

export default function SplitDialog({ deal, members, me, onClose, onSaved }: { deal: Deal; members: string[]; me: string | null; onClose: () => void; onSaved: () => void }) {
  const start = (deal.split ?? []).map((s) => ({ user_id: s.user_id, percent: String(s.percent) }));
  const [rows, setRows] = useState(start.length ? start : [{ user_id: deal.assigned_to ?? "", percent: "100" }, { user_id: "", percent: "" }]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function save() {
    const body = splitBody(rows);
    if ("error" in body) { setError(body.error); return; }
    setBusy(true);
    setError(null);
    try { await put(`ledger/${deal.id}/split`, body); onSaved(); onClose(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  const set = (i: number, patch: Partial<{ user_id: string; percent: string }>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="split-title" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md rounded-xl border border-line bg-panel p-5 shadow-xl">
        <h2 id="split-title" className="text-base font-semibold">Split the commission</h2>
        <p className="mt-1 text-sm text-muted">{deal.buyer_name ?? "Buyer"}, {deal.developer_name}. Expected commission {fmtEtb(deal.expected_commission_etb ?? 0)}. The shares must add up to 100. Leave every row empty to remove the split.</p>
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <div className="mt-3 space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <Select id={`split-member-${i}`} size="sm" className="flex-1" value={r.user_id} placeholder="Member..." onChange={(v) => set(i, { user_id: v })}
                options={members.map((m) => ({ value: m, label: memberLabel(m, me) }))} />
              <input aria-label={`Share ${i + 1} (percent)`} inputMode="decimal" placeholder="%" value={r.percent} onChange={(e) => set(i, { percent: e.target.value })} className="w-20 rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm" />
            </div>
          ))}
        </div>
        <div className="mt-2"><Button type="button" onClick={() => setRows([...rows, { user_id: "", percent: "" }])}>Add a person</Button></div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" onClick={onClose}>Cancel</Button>
          <Button type="button" variant="primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving..." : "Save"}</Button>
        </div>
      </div>
    </div>
  );
}
