import { describe, expect, it } from "vitest";
import { agencyPriceBody, hasOwnPrice, priceSource } from "./prices";

const fmt = (n: number) => `${n / 1e6}M ETB`;

describe("an agency's own price", () => {
  it("is only shown as the agency's when it has one", () => {
    expect(hasOwnPrice({ price_from_etb: 20e6, price_note: null, agency_price_etb: null })).toBe(false);
    expect(hasOwnPrice({ price_from_etb: "19500000", price_note: null, agency_price_etb: "19500000" })).toBe(true);
    expect(priceSource({ price_from_etb: 20e6, price_note: null }, fmt)).toBeNull();
  });
  it("names the shared price beside it, or says there is none", () => {
    expect(priceSource({ price_from_etb: 19.5e6, price_note: null, agency_price_etb: 19.5e6, shared_price_from_etb: 22e6 }, fmt)).toBe("Your agency's price. Shared price: from 22M ETB.");
    expect(priceSource({ price_from_etb: 19.5e6, price_note: null, agency_price_etb: 19.5e6, shared_price_from_etb: null, agency_price_until: "2026-12-31" }, fmt))
      .toBe("Your agency's price until 2026-12-31. Shared price: on request.");
  });
  it("checks the form before sending it", () => {
    expect(agencyPriceBody("19500000", " our deal ", "", "2026-11-02")).toEqual({ body: { price_etb: 19500000, note: "our deal", valid_until: null } });
    expect(agencyPriceBody("0", "", "", "2026-11-02")).toEqual({ error: "Enter a price in birr, above zero." });
    expect(agencyPriceBody("5", "", "2026-11-01", "2026-11-02")).toEqual({ error: "The end date is in the past." });
  });
});
