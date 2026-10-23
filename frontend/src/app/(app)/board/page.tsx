"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import clsx from "@/components/clsx";
import Select from "@/components/Select";
import { PageHeader, Button, Card, DEVELOPER_COLOR, DEVELOPER_VAR, ErrorNote, Pill, ScoreBar } from "@/components/ui";
import { errorMessage, patch } from "@/lib/api";
import { COLUMNS, applyMove, canMove, dealsByStatus, expectedCommission, needsContractDetails, qualifiedLabel, type ColumnId } from "@/lib/board";
import { displayName, fmtDate, fmtEtb } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { BoardData, Deal, LedgerStatus } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface Extra { contract_value_etb?: number; next_buyer_milestone_date?: string }

const STAGE_COLORS = ["#7682f4", "#a078f5", "#fbbf24", "#34d399", "#60a5fa", "#f87171"];

export default function BoardPage() {
  const { dataVersion } = useLive();
  const board = useApi<BoardData>("board", 20_000);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ deal: Deal; to: LedgerStatus } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<ColumnId | null>(null);
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) void board.reload() }, [dataVersion]);

  async function commit(deal: Deal, to: LedgerStatus, extra: Extra = {}) {
    if (!board.data) return;
    setError(null);
    board.setData(applyMove(board.data, deal.id, to, extra as Partial<Deal>)); // the card moves at once; a failed save puts it back
    try {
      await patch(`ledger/${deal.id}`, { status: to, ...extra });
    } catch (e) {
      setError(errorMessage(e));
    }
    await board.reload();
  }

  function requestMove(deal: Deal, to: LedgerStatus) {
    if (!canMove(deal, to)) return;
    if (needsContractDetails(deal, to)) setPending({ deal, to });
    else void commit(deal, to);
  }

  const data = board.data;
  const grouped = dealsByStatus(data?.deals ?? []);
  const findDeal = (id: string) => data?.deals.find((d) => d.id === id);

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Pipeline" title="Deal Board" description={<>One card per developer opportunity. Drag a card to move it, or use the menu on the card. Moving the winner forward does not close the others; drag those to Lost yourself.</>} />
      {(board.error || error) && <ErrorNote>{error ?? board.error}</ErrorNote>}

      {/* relative: keeps the hidden (sr-only) labels inside this scroller instead of widening the whole page */}
      <div className="relative -mx-4 overflow-x-auto px-4 pb-4 md:-mx-6 md:px-6">
        <div className="flex min-w-max gap-3">
          {COLUMNS.map((col, colIndex) => {
            const deals = col.id === "QUALIFIED" ? [] : grouped[col.id as LedgerStatus];
            const count = col.id === "QUALIFIED" ? data?.qualified.length ?? 0 : deals.length;
            return (
              <section
                key={col.id} aria-label={col.title}
                onDragOver={(e) => { if (col.accepts && dragging) { e.preventDefault(); setOver(col.id); } }}
                onDragLeave={() => setOver((o) => (o === col.id ? null : o))}
                onDrop={(e) => {
                  e.preventDefault(); setOver(null);
                  const deal = findDeal(e.dataTransfer.getData("text/plain"));
                  setDragging(null);
                  if (deal && col.accepts) requestMove(deal, col.id as LedgerStatus);
                }}
                className={clsx("card flex w-72 shrink-0 flex-col rounded-2xl border", over === col.id ? "border-accent ring-2 ring-accent/30" : "border-line")}
              >
                <header className="border-b border-line px-4 py-3" style={{ boxShadow: `inset 0 3px 0 ${STAGE_COLORS[colIndex % STAGE_COLORS.length]}` }}>
                  <div className="flex items-center justify-between">
                    <h2 className="flex items-center gap-2 text-sm font-semibold"><span className="h-2 w-2 rounded-full" style={{ background: STAGE_COLORS[colIndex % STAGE_COLORS.length] }} />{col.title}</h2>
                    <Pill>{count}</Pill>
                  </div>
                  <p className="text-xs text-muted">{col.hint}</p>
                  {col.id !== "QUALIFIED" && deals.length > 0 && <p className="tnum mt-1 text-xs text-muted">{fmtEtb(expectedCommission(deals))} expected</p>}
                </header>
                <div className="min-h-[6rem] flex-1 space-y-2 p-2">
                  {col.id === "QUALIFIED"
                    ? (data?.qualified ?? []).map((b) => (
                        <article key={b.id} className="rounded-xl border border-line bg-raised p-3.5 text-sm transition-colors hover:border-[#3a3a62]">
                          <div className="flex items-center justify-between gap-2">
                            <Link href="/crm" className="truncate font-medium hover:text-accent">{displayName(b)}</Link>
                            {b.human_takeover && <Pill tone="warn">You</Pill>}
                          </div>
                          <p className="text-xs text-muted">{qualifiedLabel(b)}</p>
                          <p className="mt-1 text-xs">{b.max_budget_etb ? fmtEtb(Number(b.max_budget_etb)) : "No budget"}{b.min_bedrooms !== null ? ` - ${b.min_bedrooms}+ BR` : ""}</p>
                          {b.preferred_sub_cities.length > 0 && <p className="text-xs text-muted">{b.preferred_sub_cities.join(", ")}</p>}
                          <div className="mt-2"><ScoreBar score={b.lead_score} /></div>
                        </article>
                      ))
                    : deals.map((d) => (
                        <article
                          key={d.id} draggable
                          onDragStart={(e) => { e.dataTransfer.setData("text/plain", d.id); e.dataTransfer.effectAllowed = "move"; setDragging(d.id); }}
                          onDragEnd={() => { setDragging(null); setOver(null); }}
                          style={{ borderLeft: `3px solid ${DEVELOPER_VAR[d.developer_slug] ? `rgb(${DEVELOPER_VAR[d.developer_slug]})` : "transparent"}` }}
                          className={clsx("cursor-grab rounded-xl border border-line bg-raised p-3.5 text-sm transition-colors hover:border-[#3a3a62] active:cursor-grabbing", dragging === d.id && "opacity-40")}
                        >
                          <p className="font-medium">{d.buyer_name ?? "Buyer"}</p>
                          <p className={clsx("text-xs font-medium", DEVELOPER_COLOR[d.developer_slug])}>{d.developer_name}</p>
                          <p className="text-xs text-muted">{d.property_title}</p>
                          <p className="tnum mt-1 text-xs">{fmtEtb(d.property_price_etb)} <span className="text-muted">- commission</span> {fmtEtb(d.expected_commission_etb)}</p>
                          {d.next_buyer_milestone_date && <p className="mt-1 text-xs text-warn">Payment due {fmtDate(d.next_buyer_milestone_date)}</p>}
                          {d.tour_id && <Link href={`/tours/${d.tour_id}`} className="mt-1 inline-block text-xs font-medium text-accent hover:underline">Pre-tour dossier</Link>}
                          <div className="mt-2 flex items-center justify-between gap-2">
                            <ScoreBar score={d.lead_score} />
                            <label className="sr-only" htmlFor={`move-${d.id}`}>Move deal for {d.buyer_name} to</label>
                            <Select
                              id={`move-${d.id}`} size="sm" className="max-w-[9rem]" menuWidth="content" value="" placeholder="Move to..."
                              onChange={(v) => { if (v) requestMove(d, v as LedgerStatus); }}
                              options={COLUMNS.filter((c) => c.accepts && c.id !== d.status).map((c) => ({ value: c.id, label: c.title }))}
                            />
                          </div>
                        </article>
                      ))}
                  {count === 0 && <p className="px-2 py-4 text-center text-xs text-muted">{col.accepts ? "Drop a deal here" : "No buyers waiting"}</p>}
                </div>
              </section>
            );
          })}
        </div>
      </div>

      {pending && (
        <ContractDialog
          deal={pending.deal}
          onCancel={() => setPending(null)}
          onSave={(extra) => { const { deal, to } = pending; setPending(null); void commit(deal, to, extra); }}
        />
      )}
    </div>
  );
}

function ContractDialog({ deal, onCancel, onSave }: { deal: Deal; onCancel: () => void; onSave: (extra: Extra) => void }) {
  const [value, setValue] = useState(String(deal.contract_value_etb ?? deal.property_price_etb ?? ""));
  const [date, setDate] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const valid = Number(value) > 0 && /^\d{4}-\d{2}-\d{2}$/.test(date);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="contract-title" onClick={onCancel}>
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); if (valid) onSave({ contract_value_etb: Number(value), next_buyer_milestone_date: date }); }}
        className="w-full max-w-sm rounded-xl border border-line bg-panel p-5 shadow-xl"
      >
        <h2 id="contract-title" className="text-base font-semibold">Contract signed</h2>
        <p className="mt-1 text-sm text-muted">{deal.buyer_name} signed with {deal.developer_name}. The payment date drives the reminders sent to the buyer.</p>
        <label htmlFor="contract-value" className="mt-4 block text-sm font-medium">Contract value (ETB)</label>
        <input id="contract-value" type="number" min="1" step="1" value={value} onChange={(e) => setValue(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm" />
        <label htmlFor="milestone-date" className="mt-3 block text-sm font-medium">Next payment due</label>
        <input id="milestone-date" type="date" autoFocus required value={date} onChange={(e) => setDate(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm" />
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!valid}>Save</Button>
        </div>
      </form>
    </div>
  );
}
