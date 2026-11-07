import { describe, expect, it } from "vitest";
import { termBody } from "./terms";

describe("termBody", () => {
  it("accepts a rate, with a comma or a dot, and trims the note", () => {
    expect(termBody("2,5", "  agreed  ", "")).toEqual({ body: { rate_pct: 2.5, note: "agreed", valid_until: null } });
    expect(termBody("4", "", "2999-01-01")).toEqual({ body: { rate_pct: 4, note: null, valid_until: "2999-01-01" } });
  });
  it("refuses a missing, negative or too large rate", () => {
    for (const bad of ["", "abc", "-1", "101"]) expect(termBody(bad, "", "")).toEqual({ error: "Enter a rate between 0 and 100." });
  });
  it("refuses an end date in the past", () => {
    expect(termBody("3", "", "2000-01-01")).toEqual({ error: "The end date is in the past." });
  });
});
