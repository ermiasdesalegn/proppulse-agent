import type { BoardData, Deal, LedgerStatus, QualifiedBuyer } from "./types";

export type ColumnId = "QUALIFIED" | LedgerStatus;

export interface Column {
  id: ColumnId;
  title: string;
  hint: string;
  /** Only deal rows can be dragged; "Lead Qualified" holds buyers who have no deal yet. */
  accepts: boolean;
}

export const COLUMNS: Column[] = [
  { id: "QUALIFIED", title: "Lead Qualified", hint: "Buyers with a budget and no site visit yet", accepts: false },
  { id: "LEAD_REGISTERED", title: "Site Visit Booked", hint: "Lead registered with the developer", accepts: true },
  { id: "TOUR_COMPLETED", title: "Tour Completed", hint: "The buyer has seen the unit", accepts: true },
  { id: "CONTRACT_SIGNED", title: "Contract Signed", hint: "Signed with the developer", accepts: true },
  { id: "COMMISSION_PENDING", title: "Commission Pending", hint: "Developer is releasing the commission", accepts: true },
  { id: "COMMISSION_PAID", title: "Commission Paid", hint: "Collected", accepts: true },
  { id: "CLOSED_LOST", title: "Lost", hint: "Another developer won, or the buyer walked away", accepts: true },
];

export function dealsByStatus(deals: Deal[]): Record<LedgerStatus, Deal[]> {
  const out: Record<LedgerStatus, Deal[]> = {
    LEAD_REGISTERED: [], TOUR_COMPLETED: [], CONTRACT_SIGNED: [], COMMISSION_PENDING: [], COMMISSION_PAID: [], CLOSED_LOST: [],
  };
  for (const deal of deals) out[deal.status]?.push(deal);
  return out;
}

/** Signing needs a payment milestone date: the reminder job runs on it. Ask for it when it is not on the deal yet. */
export function needsContractDetails(deal: Deal, to: LedgerStatus): boolean {
  return to === "CONTRACT_SIGNED" && !deal.next_buyer_milestone_date;
}

export function canMove(deal: Deal, to: LedgerStatus): boolean {
  return deal.status !== to;
}

/** The board as it will look once the move is saved, so the card jumps columns immediately. */
export function applyMove(board: BoardData, dealId: string, to: LedgerStatus, extra: Partial<Deal> = {}): BoardData {
  return { ...board, deals: board.deals.map((d) => (d.id === dealId ? { ...d, ...extra, status: to } : d)) };
}

export function expectedCommission(deals: Deal[]): number {
  return deals.reduce((sum, d) => sum + (d.expected_commission_etb ?? 0), 0);
}

/** Deals still worth chasing: not lost and not already collected. */
export function openPipeline(deals: Deal[]): Deal[] {
  return deals.filter((d) => d.status !== "CLOSED_LOST" && d.status !== "COMMISSION_PAID");
}

export function qualifiedLabel(buyer: QualifiedBuyer): string {
  return buyer.deal_stage === "INVENTORY_PENDING" ? "Waiting for inventory" : "Qualified";
}
