import { describe, expect, it } from "vitest";
import { canSendClaim, showsClaim } from "./claims";

describe("claims", () => {
  it("can be sent once the contract is signed, until paid or acknowledged", () => {
    expect(canSendClaim({ status: "LEAD_REGISTERED", claim_status: null })).toBe(false);
    expect(canSendClaim({ status: "CONTRACT_SIGNED", claim_status: null })).toBe(true);
    expect(canSendClaim({ status: "COMMISSION_PENDING", claim_status: "DISPUTED" })).toBe(true);
    expect(canSendClaim({ status: "COMMISSION_PENDING", claim_status: "ACKNOWLEDGED" })).toBe(false);
    expect(canSendClaim({ status: "COMMISSION_PAID", claim_status: null })).toBe(false);
  });
  it("is shown for a paid deal only when it has a claim to read", () => {
    expect(showsClaim({ status: "COMMISSION_PAID", claim_status: null })).toBe(false);
    expect(showsClaim({ status: "COMMISSION_PAID", claim_status: "ACKNOWLEDGED" })).toBe(true);
  });
});
