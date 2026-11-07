export type VisitStatus = "upcoming" | "past" | "cancelled";

export interface Visit {
  id: string;
  scheduled_start: string;
  kind: "SITE_VISIT" | "CONSULTATION";
  status: VisitStatus;
  can_cancel: boolean;
  homes: string[];
  project: string | null;
}

/** What a visit is about, in words: the homes on the route, the project, or the phone call. */
export function visitTitle(v: Pick<Visit, "kind" | "homes" | "project">): string {
  if (v.kind === "CONSULTATION") return "Phone consultation";
  if (v.homes.length === 1) return v.homes[0];
  if (v.homes.length > 1) return `${v.homes[0]} and ${v.homes.length - 1} more`;
  return v.project ? `Visit to ${v.project}` : "Site visit";
}

export const STATUS_LABEL: Record<VisitStatus, string> = { upcoming: "Upcoming", past: "Done", cancelled: "Cancelled" };

/** Upcoming visits first (soonest first), then the rest, newest first. */
export function sortVisits(items: Visit[]): Visit[] {
  const t = (v: Visit) => new Date(v.scheduled_start).getTime();
  const up = items.filter((v) => v.status === "upcoming").sort((a, b) => t(a) - t(b));
  const rest = items.filter((v) => v.status !== "upcoming").sort((a, b) => t(b) - t(a));
  return [...up, ...rest];
}
