// The demo billing screens (backend migration 038, app/billing.py). Nothing here moves real money; every screen says so.

export const DEMO_BANNER = "DEMO MODE: no real money moves";

export type PlanName = "free" | "pro" | "business";
export const PLAN_ORDER: PlanName[] = ["free", "pro", "business"];

/** The plan named in a checkout address, or null when it is not one that can be bought ("free" cannot). */
export function purchasablePlan(value: string | null | undefined): "pro" | "business" | null {
  return value === "pro" || value === "business" ? value : null;
}

/** True when `target` is a higher plan than `current`: the only change the checkout accepts. */
export function isUpgrade(current: string, target: string): boolean {
  const a = PLAN_ORDER.indexOf(current as PlanName);
  const b = PLAN_ORDER.indexOf(target as PlanName);
  return a >= 0 && b > a;
}

/** How much of a cap is used, 0 to 100, for a bar. A missing or zero cap shows as empty rather than dividing by zero. */
export function usagePercent(used: number, limit: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
}

/** The bar's tone: calm, then a warning near the cap, then bad at it. */
export function usageTone(percent: number): "good" | "warn" | "bad" {
  return percent >= 100 ? "bad" : percent >= 80 ? "warn" : "good";
}

/** A price as the screens show it, always with the currency and "demo". */
export function demoPrice(etb: number): string {
  return etb === 0 ? "Free" : `${Math.round(etb).toLocaleString("en-US")} ETB / month (demo)`;
}
