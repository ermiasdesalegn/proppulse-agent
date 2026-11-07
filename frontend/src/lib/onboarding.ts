// The "Get started" card on the console Overview and the agency rename rule. Pure helpers, so they can be tested without a page.

export interface Onboarding {
  org_id: string;
  telegram: boolean;
  price: boolean;
  commission: boolean;
  teammate: boolean;
}

export interface OnboardingStep {
  key: "telegram" | "price" | "commission" | "teammate";
  title: string;
  hint: string;
  href: string;
  done: boolean;
}

/** The four first steps in the order a new agency does them, each with the page that does it. */
export function onboardingSteps(p: Onboarding): OnboardingStep[] {
  return [
    { key: "telegram", title: "Connect your Telegram bot", hint: "Buyers message it and replies come from it.", href: "/console/agency#telegram", done: p.telegram },
    { key: "price", title: "Set your own price for a unit or project", hint: "Your buyers see your price, not the shared one.", href: "/console/projects", done: p.price },
    { key: "commission", title: "Set a commission rate with a developer", hint: "Expected commissions then use the rate you agreed.", href: "/console/terms", done: p.commission },
    { key: "teammate", title: "Invite a teammate", hint: "Brokers and viewers join by a link.", href: "/console/agency#team", done: p.teammate },
  ];
}

export function allDone(p: Onboarding): boolean {
  return onboardingSteps(p).every((s) => s.done);
}

/** Dismissal is remembered in this browser, per agency (no database column). */
export function dismissKey(orgId: string): string {
  return `pp-onboarding-dismissed:${orgId}`;
}

/** Show the card to an admin until everything is done or it was dismissed. */
export function showOnboarding(p: Onboarding | null, dismissed: boolean): boolean {
  return p !== null && !dismissed && !allDone(p);
}

/** The agency's new display name: trimmed, inner whitespace collapsed, 2 to 80 characters. Mirrors the backend rule. */
export function agencyNameBody(raw: string): { name: string } | { error: string } {
  const name = raw.split(/\s+/).filter(Boolean).join(" ");
  if (name.length < 2 || name.length > 80) return { error: "The name must be 2 to 80 characters." };
  return { name };
}
