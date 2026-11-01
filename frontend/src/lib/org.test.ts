import { describe, expect, it } from "vitest";
import { atLeast, chooseOrg, cleanOrgId, parseOrgs, slugPreview } from "./org";

const a = { id: "11111111-1111-4111-8111-111111111111", slug: "alpha", name: "Alpha", role: "owner" as const };
const b = { id: "22222222-2222-4222-8222-222222222222", slug: "beta", name: "Beta", role: "viewer" as const };

describe("parseOrgs", () => {
  it("reads the backend's list", () => {
    expect(parseOrgs({ organizations: [{ ...a, plan: "free", status: "active" }, b] })).toEqual([a, b]);
  });

  it("drops anything malformed instead of trusting it", () => {
    expect(parseOrgs({ organizations: [{ id: 1 }, null, "x", { ...a, role: "god" }, { ...b, name: undefined }, b] })).toEqual([b]);
    expect(parseOrgs(null)).toEqual([]);
    expect(parseOrgs({ organizations: "no" })).toEqual([]);
    expect(parseOrgs({})).toEqual([]);
  });
});

describe("chooseOrg", () => {
  it("keeps the remembered organization while the person still belongs to it", () => {
    expect(chooseOrg([a, b], b.id)).toBe(b);
  });

  it("falls back to the first when the remembered one is gone or unknown", () => {
    expect(chooseOrg([a, b], "33333333-3333-4333-8333-333333333333")).toBe(a);
    expect(chooseOrg([a, b], null)).toBe(a);
  });

  it("chooses nothing for someone with no organization", () => {
    expect(chooseOrg([], a.id)).toBeNull();
  });
});

describe("atLeast", () => {
  it("orders the roles", () => {
    expect(atLeast("owner", "admin")).toBe(true);
    expect(atLeast("agent", "agent")).toBe(true);
    expect(atLeast("viewer", "agent")).toBe(false);
    expect(atLeast(null, "viewer")).toBe(false);
    expect(atLeast(undefined, "viewer")).toBe(false);
  });
});

describe("cleanOrgId", () => {
  it("accepts only something shaped like an id", () => {
    expect(cleanOrgId(" 11111111-1111-4111-8111-111111111111 ")).toBe(a.id);
    expect(cleanOrgId("11111111-1111-4111-8111-11111111111G")).toBeNull();
    expect(cleanOrgId("x\r\nx-admin-key: 1")).toBeNull();
    expect(cleanOrgId("")).toBeNull();
    expect(cleanOrgId(undefined)).toBeNull();
  });
});

describe("slugPreview", () => {
  it("makes a tidy address from a name", () => {
    expect(slugPreview("Sheger Homes!")).toBe("sheger-homes");
    expect(slugPreview("  --Bole & Co.  ")).toBe("bole-co");
    expect(slugPreview("ሰላም")).toBe("");
    expect(slugPreview("a".repeat(60)).length).toBe(39);
  });
});
