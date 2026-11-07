import type { Deal } from "./types";

export type ClaimStatus = "SENT" | "ACKNOWLEDGED" | "DISPUTED";

export const CLAIM_LABEL: Record<ClaimStatus, string> = { SENT: "Claim sent", ACKNOWLEDGED: "Acknowledged", DISPUTED: "Disputed" };
export const CLAIM_TONE: Record<ClaimStatus, "warn" | "good" | "bad"> = { SENT: "warn", ACKNOWLEDGED: "good", DISPUTED: "bad" };

export const CLAIM_EVENT_LABEL: Record<string, string> = {
  claim_sent: "Claim sent to the developer",
  acknowledged: "Developer acknowledged the claim",
  disputed: "Developer disputed the claim",
  overdue_flagged: "Flagged as pending too long",
};

/** A claim can be sent from the signed contract until the commission is paid, and not once the developer has acknowledged it. */
export function canSendClaim(deal: Pick<Deal, "status" | "claim_status">): boolean {
  return (deal.status === "CONTRACT_SIGNED" || deal.status === "COMMISSION_PENDING") && deal.claim_status !== "ACKNOWLEDGED";
}

/** Whether the deal card offers the claim dialog at all: while a claim can be sent, or to read one that was. */
export function showsClaim(deal: Pick<Deal, "status" | "claim_status">): boolean {
  return canSendClaim(deal) || deal.claim_status != null;
}
