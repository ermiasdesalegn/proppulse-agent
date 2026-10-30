import { describe, expect, it } from "vitest";
import { inAddis, spotKey } from "./geo";

describe("map location checks", () => {
  it("keeps listings inside Addis Ababa and drops mis-geocoded ones", () => {
    expect(inAddis(9.0107, 38.7613)).toBe(true); // Meskel Square
    expect(inAddis(8.9575, 38.8702)).toBe(true);
    expect(inAddis(29.679415, -95.3939551)).toBe(false); // a Lebu listing that landed in Texas
    expect(inAddis(9.7863766, 33.353137796)).toBe(false); // somewhere in Sudan
    expect(inAddis(0, 0)).toBe(false);
  });

  it("gives homes at the same spot, to about 100 m, the same key", () => {
    expect(spotKey(9.0231, 38.8692)).toBe(spotKey(9.0233, 38.8694));
    expect(spotKey(9.0231, 38.8692)).not.toBe(spotKey(9.0301, 38.8692));
  });
});
