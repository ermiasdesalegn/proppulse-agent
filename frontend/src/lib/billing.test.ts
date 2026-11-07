import { describe, expect, it } from "vitest";
import { demoPrice, isUpgrade, purchasablePlan, usagePercent, usageTone } from "./billing";

describe("billing helpers", () => {
  it("only pro and business can be bought", () => {
    expect(purchasablePlan("pro")).toBe("pro");
    expect(purchasablePlan("business")).toBe("business");
    expect(purchasablePlan("free")).toBeNull();
    expect(purchasablePlan("enterprise")).toBeNull();
    expect(purchasablePlan(undefined)).toBeNull();
  });
  it("only a higher plan is an upgrade", () => {
    expect(isUpgrade("free", "pro")).toBe(true);
    expect(isUpgrade("pro", "business")).toBe(true);
    expect(isUpgrade("pro", "pro")).toBe(false);
    expect(isUpgrade("business", "pro")).toBe(false);
    expect(isUpgrade("free", "nonsense")).toBe(false);
  });
  it("turns usage into a bounded percentage and a tone", () => {
    expect(usagePercent(50, 100)).toBe(50);
    expect(usagePercent(250, 100)).toBe(100);
    expect(usagePercent(5, 0)).toBe(0);
    expect(usagePercent(-3, 100)).toBe(0);
    expect(usageTone(40)).toBe("good");
    expect(usageTone(85)).toBe("warn");
    expect(usageTone(100)).toBe("bad");
  });
  it("always says a price is a demo", () => {
    expect(demoPrice(0)).toBe("Free");
    expect(demoPrice(2500)).toBe("2,500 ETB / month (demo)");
  });
});
