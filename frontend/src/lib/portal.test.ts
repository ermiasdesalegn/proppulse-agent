import { describe, expect, it } from "vitest";
import { apiQuery, coverSrc, deliveryLabel, describePlan, filtersToParams, hasFilters, parseFilters, pricePerSqm, specsLine } from "./portal";

describe("parseFilters", () => {
  it("reads good values and drops bad ones", () => {
    const f = parseFilters({ q: "  bole ", sub_city: "Bole", min_bedrooms: "3", price_max: "25000000", sort: "price_asc", page: "2", developer: "ayat" });
    expect(f).toMatchObject({ q: "bole", sub_city: "Bole", min_bedrooms: 3, price_max: 25_000_000, price_min: null, sort: "price_asc", page: 2, developer: "ayat" });
    const bad = parseFilters({ min_bedrooms: "-1", price_min: "abc", price_max: "1e9", sort: "random", page: "0", developer: "A B;drop", q: ["x", "y"] });
    expect(bad).toMatchObject({ min_bedrooms: null, price_min: null, price_max: null, sort: "newest", page: 1, developer: "", q: "x" });
  });

  it("caps text length and refuses absurd numbers", () => {
    expect(parseFilters({ q: "a".repeat(500) }).q).toHaveLength(80);
    expect(parseFilters({ min_bedrooms: "999" }).min_bedrooms).toBeNull();
    expect(parseFilters({ page: "99999999" }).page).toBe(1);
  });

  it("starts with nothing filtered", () => {
    const f = parseFilters({});
    expect(hasFilters(f)).toBe(false);
    expect(filtersToParams(f).toString()).toBe("");
  });
});

describe("addresses", () => {
  const f = parseFilters({ sub_city: "Summit Figa", min_bedrooms: "2", sort: "price_desc", page: "3" });

  it("round-trips through the address and can change one thing", () => {
    expect(parseFilters(Object.fromEntries(filtersToParams(f)))).toEqual(f);
    expect(filtersToParams(f, { sub_city: "", page: 1 }).toString()).toBe("min_bedrooms=2&sort=price_desc");
  });

  it("asks the backend for a page as a limit and an offset", () => {
    expect(apiQuery(f)).toBe("sub_city=Summit+Figa&min_bedrooms=2&sort=price_desc&limit=24&offset=48");
    expect(apiQuery(parseFilters({}), 60)).toBe("limit=60");
  });
});

describe("wording", () => {
  it("describes the payment terms the listing states", () => {
    expect(describePlan({ down_payment_pct: 20, payment_plan: { installments: 24, interval_months: 1, handover_pct: 10 } })).toBe("20% down payment, 24 instalments every month, 10% at handover");
    expect(describePlan({ down_payment_pct: "30.00", payment_plan: null })).toBe("30% down payment");
    expect(describePlan({ down_payment_pct: null, payment_plan: { installments: 8, interval_months: 3 } })).toBe("8 instalments every 3 months");
    expect(describePlan({ down_payment_pct: null, payment_plan: null })).toBeNull();
  });

  it("builds a specs line and a price per square metre from decimals sent as text", () => {
    expect(specsLine({ bedrooms: 3, sqm: "135.00", sub_city: "Bole" })).toBe("3 bed · 135 m² · Bole");
    expect(specsLine({ bedrooms: 0, sqm: 0, sub_city: "Ayat" })).toBe("Studio · Ayat");
    expect(pricePerSqm({ price_etb: "19800000.00", sqm: "135.00" })).toBe(146667);
    expect(pricePerSqm({ price_etb: 1, sqm: 0 })).toBeNull();
  });

  it("labels delivery dates and ignores bad ones", () => {
    expect(deliveryLabel("2999-03-01")).toBe("Delivery Mar 2999");
    expect(deliveryLabel("2001-03-01")).toBe("Ready to move in");
    expect(deliveryLabel("not a date")).toBeNull();
    expect(deliveryLabel(null)).toBeNull();
  });

  it("only uses cover addresses it understands", () => {
    expect(coverSrc("/api/public/covers/abc")).toBe("/api/public/covers/abc");
    expect(coverSrc("https://site.example/a.jpg")).toBe("https://site.example/a.jpg");
    for (const bad of ["javascript:alert(1)", "//evil.example/x.png", "/etc/passwd", "", null]) expect(coverSrc(bad)).toBeNull();
  });
});
