"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, ErrorNote, Pill } from "@/components/ui";
import { api, errorMessage, post, put } from "@/lib/api";
import { CLAIM_EVENT_LABEL, CLAIM_LABEL, CLAIM_TONE, canSendClaim, type ClaimStatus } from "@/lib/claims";
import { fmtDate, fmtEtb } from "@/lib/format";
import type { Deal } from "@/lib/types";

interface ClaimView {
  claim_status: ClaimStatus | null;
  claim_sent_at: string | null;
  claim_to: string | null;
  overdue_flagged_at: string | null;
  events: { action: string; note: string | null; at: string }[];
}

/** The claim to the developer for one deal: send it, record the developer's answer, read its history. The server decides who may (agent role or higher). */
export default function ClaimDialog({ deal, onClose, onChanged }: { deal: Deal; onClose: () => void; onChanged: () => void }) {
  const [claim, setClaim] = useState<ClaimView | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setClaim(await api<ClaimView>(`ledger/${deal.id}/claim`)); } catch (e) { setError(errorMessage(e)); }
  }, [deal.id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function run(work: () => Promise<ClaimView>) {
    setBusy(true);
    setError(null);
    try { setClaim(await work()); setNote(""); onChanged(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  const status = claim?.claim_status ?? deal.claim_status;
  const live = { ...deal, claim_status: status };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="claim-title" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-line bg-panel p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <h2 id="claim-title" className="text-base font-semibold">Commission claim</h2>
          {status && <Pill tone={CLAIM_TONE[status]}>{CLAIM_LABEL[status]}</Pill>}
        </div>
        <p className="mt-1 text-sm text-muted">{deal.buyer_name ?? "Buyer"} with {deal.developer_name}, {deal.property_title}. Expected commission {fmtEtb(deal.expected_commission_etb ?? 0)}.</p>
        {claim?.overdue_flagged_at && <p className="mt-2 text-xs text-warn">This commission has been pending longer than expected.</p>}
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}

        {canSendClaim(live) && (
          <div className="mt-4">
            <label htmlFor="claim-note" className="block text-sm font-medium">Message to the developer (optional)</label>
            <textarea id="claim-note" rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm" />
            <p className="mt-1 text-xs text-muted">Emailed to the developer&apos;s sales contact from your agency&apos;s name. Replies come to your own address.</p>
            <Button variant="primary" className="mt-2" disabled={busy} onClick={() => void run(() => post<ClaimView>(`ledger/${deal.id}/claim`, { note }))}>
              {busy ? "Sending..." : status ? "Send the claim again" : "Send claim"}
            </Button>
          </div>
        )}

        {status && status !== "ACKNOWLEDGED" && (
          <div className="mt-4 border-t border-line pt-3">
            <p className="text-sm font-medium">What did the developer answer?</p>
            <div className="mt-2 flex gap-2">
              <Button disabled={busy} onClick={() => void run(() => put<ClaimView>(`ledger/${deal.id}/claim`, { status: "ACKNOWLEDGED", note: note || null }))}>Acknowledged</Button>
              {status !== "DISPUTED" && <Button variant="danger" disabled={busy} onClick={() => void run(() => put<ClaimView>(`ledger/${deal.id}/claim`, { status: "DISPUTED", note: note || null }))}>Disputed</Button>}
            </div>
          </div>
        )}

        {claim && claim.events.length > 0 && (
          <ol className="mt-4 space-y-1.5 border-t border-line pt-3 text-xs">
            {claim.events.map((ev, i) => (
              <li key={i}><span className="text-muted">{fmtDate(ev.at)}</span> {CLAIM_EVENT_LABEL[ev.action] ?? ev.action}{ev.note ? <span className="text-muted">: {ev.note}</span> : null}</li>
            ))}
          </ol>
        )}
        <div className="mt-5 flex justify-end"><Button onClick={onClose}>Close</Button></div>
      </div>
    </div>
  );
}
