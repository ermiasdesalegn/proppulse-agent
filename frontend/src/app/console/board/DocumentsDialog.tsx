"use client";

import { useCallback, useEffect, useState } from "react";
import Select from "@/components/Select";
import { Button, ErrorNote } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { DOCUMENT_KINDS, DOCUMENT_TYPES, checkDocument, fmtSize } from "@/lib/documents";
import { fmtDate } from "@/lib/format";
import type { Deal } from "@/lib/types";

interface Doc { id: string; kind: string; filename: string; size_bytes: number; created_at: string }

/** The contract, receipts and the developer's acknowledgement for one deal. Files are private: they open only through the console. */
export default function DocumentsDialog({ deal, onClose }: { deal: Deal; onClose: () => void }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [kind, setKind] = useState("receipt");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setDocs((await api<{ documents: Doc[] }>(`ledger/${deal.id}/documents`)).documents); } catch (e) { setError(errorMessage(e)); }
  }, [deal.id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function upload(file: File | undefined) {
    if (!file) return;
    const problem = checkDocument(file);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError(null);
    try {
      await api(`ledger/${deal.id}/documents?kind=${encodeURIComponent(kind)}&filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file, headers: { "content-type": file.type } });
      await load();
    } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }
  async function remove(doc: Doc) {
    if (!window.confirm(`Delete ${doc.filename}? This cannot be undone.`)) return;
    setBusy(true);
    setError(null);
    try { await api(`ledger/${deal.id}/documents/${doc.id}`, { method: "DELETE" }); await load(); } catch (e) { setError(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="docs-title" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-line bg-panel p-5 shadow-xl">
        <h2 id="docs-title" className="text-base font-semibold">Deal documents</h2>
        <p className="mt-1 text-sm text-muted">{deal.buyer_name ?? "Buyer"}, {deal.developer_name}. PDF or image, up to 10 MB each. Kept privately; only members of your agency can open them.</p>
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <ul className="mt-3 space-y-2">
          {docs === null && <li className="text-sm text-muted">Loading...</li>}
          {docs?.length === 0 && <li className="text-sm text-muted">Nothing attached yet.</li>}
          {docs?.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm">
              <div className="min-w-0">
                <a href={`/api/proxy/ledger/${deal.id}/documents/${d.id}`} download className="block truncate font-medium text-accent hover:underline">{d.filename}</a>
                <span className="text-xs text-muted">{DOCUMENT_KINDS.find((k) => k.value === d.kind)?.label ?? d.kind} · {fmtSize(d.size_bytes)} · {fmtDate(d.created_at)}</span>
              </div>
              <Button disabled={busy} onClick={() => void remove(d)}>Delete</Button>
            </li>
          ))}
        </ul>
        <div className="mt-4 border-t border-line pt-3">
          <label htmlFor="doc-kind" className="block text-sm font-medium">Attach a file</label>
          <div className="mt-1 flex items-center gap-2">
            <Select id="doc-kind" size="sm" className="w-48" value={kind} onChange={(v) => setKind(v || "other")} options={DOCUMENT_KINDS} />
            <input aria-label="Choose a file" type="file" accept={DOCUMENT_TYPES.join(",")} disabled={busy} onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} className="min-w-0 flex-1 text-sm" />
          </div>
        </div>
        <div className="mt-5 flex justify-end"><Button onClick={onClose}>Close</Button></div>
      </div>
    </div>
  );
}
