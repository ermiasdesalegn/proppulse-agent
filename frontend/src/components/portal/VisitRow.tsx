"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { fmtDateTime } from "@/lib/format";
import { STATUS_LABEL, visitTitle, type Visit } from "@/lib/visits";

export default function VisitRow({ visit }: { visit: Visit }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function cancel() {
    if (busy || !window.confirm("Cancel this visit? The broker will be told.")) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/me/visits/${visit.id}/cancel`, { method: "POST" });
      if (res.status === 401) { window.location.href = "/login"; return; }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : "We could not cancel this visit.");
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  const tone = visit.status === "upcoming" ? "bg-accent/10 text-accent" : visit.status === "cancelled" ? "bg-ink/5 text-muted line-through" : "bg-ink/5 text-muted";
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-white px-5 py-4">
      <div className="min-w-0">
        <p className="font-semibold">{visitTitle(visit)}</p>
        <p className="text-sm text-muted">{fmtDateTime(visit.scheduled_start)}</p>
        {error && <p role="alert" className="mt-1 text-sm text-[#b42318]">{error}</p>}
      </div>
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone}`}>{STATUS_LABEL[visit.status]}</span>
        {visit.can_cancel && (
          <button type="button" onClick={cancel} disabled={busy} className="rounded-full border border-ink/20 px-4 py-1.5 text-sm font-semibold transition hover:border-ink/50 disabled:opacity-50">
            {busy ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>
    </li>
  );
}
