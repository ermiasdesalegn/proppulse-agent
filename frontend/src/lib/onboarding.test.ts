import { describe, expect, it } from "vitest";
import { agencyNameBody, allDone, dismissKey, onboardingSteps, showOnboarding, type Onboarding } from "./onboarding";

const base: Onboarding = { org_id: "o1", telegram: false, price: false, commission: false, teammate: false };

describe("onboardingSteps", () => {
  it("lists four steps in order, each with a page to do it on", () => {
    const steps = onboardingSteps(base);
    expect(steps.map((s) => s.key)).toEqual(["telegram", "price", "commission", "teammate"]);
    expect(steps.every((s) => s.href.startsWith("/console/") && !s.done)).toBe(true);
  });
  it("ticks only what is done", () => {
    expect(onboardingSteps({ ...base, price: true, teammate: true }).filter((s) => s.done).map((s) => s.key)).toEqual(["price", "teammate"]);
  });
});

describe("showOnboarding", () => {
  it("shows until everything is done or it is dismissed", () => {
    expect(showOnboarding(base, false)).toBe(true);
    expect(showOnboarding(base, true)).toBe(false);
    expect(showOnboarding({ ...base, telegram: true, price: true, commission: true, teammate: true }, false)).toBe(false);
    expect(showOnboarding(null, false)).toBe(false);
  });
  it("is not all done with one step missing", () => {
    expect(allDone({ ...base, telegram: true, price: true, commission: true })).toBe(false);
  });
  it("keeps the dismissal per agency", () => {
    expect(dismissKey("a")).not.toBe(dismissKey("b"));
  });
});

describe("agencyNameBody", () => {
  it("trims and collapses spaces", () => {
    expect(agencyNameBody("  Bole   Homes ")).toEqual({ name: "Bole Homes" });
  });
  it("refuses names under 2 or over 80 characters", () => {
    for (const bad of ["", "   ", " x ", "n".repeat(81)]) expect(agencyNameBody(bad)).toEqual({ error: "The name must be 2 to 80 characters." });
    expect(agencyNameBody("n".repeat(80))).toEqual({ name: "n".repeat(80) });
  });
});
