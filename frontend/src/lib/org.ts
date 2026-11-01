// Which organization a signed-in person is acting for. Pure helpers, so the rules can be tested without a request.

export const ORG_COOKIE = "pp_org";

export type OrgRole = "viewer" | "agent" | "admin" | "owner";
const ROLE_ORDER: OrgRole[] = ["viewer", "agent", "admin", "owner"];

export interface Org {
  id: string;
  slug: string;
  name: string;
  role: OrgRole;
}

/** What `GET /api/orgs/mine` answers, with anything malformed dropped rather than trusted. */
export function parseOrgs(body: unknown): Org[] {
  const list = body && typeof body === "object" ? (body as { organizations?: unknown }).organizations : null;
  if (!Array.isArray(list)) return [];
  const orgs: Org[] = [];
  for (const raw of list) {
    if (!raw || typeof raw !== "object") continue;
    const o = raw as Record<string, unknown>;
    if (typeof o.id !== "string" || typeof o.slug !== "string" || typeof o.name !== "string") continue;
    if (!ROLE_ORDER.includes(o.role as OrgRole)) continue;
    orgs.push({ id: o.id, slug: o.slug, name: o.name, role: o.role as OrgRole });
  }
  return orgs;
}

/** The organization to act for: the remembered one if the person still belongs to it, else their first. */
export function chooseOrg(orgs: Org[], remembered: string | null | undefined): Org | null {
  if (orgs.length === 0) return null;
  return orgs.find((o) => o.id === remembered) ?? orgs[0];
}

/** Does `role` reach at least `needed`? Mirrors the backend's ordering; the backend is what actually enforces it. */
export function atLeast(role: OrgRole | null | undefined, needed: OrgRole): boolean {
  return role != null && ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(needed);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A cookie value is only passed on as an organization id when it looks like one, so it can never smuggle anything into a header. */
export function cleanOrgId(value: string | null | undefined): string | null {
  const v = (value ?? "").trim();
  return UUID.test(v) ? v.toLowerCase() : null;
}

/** A short address for a new agency, offered while the person types its name. The backend makes the real one. */
export function slugPreview(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 39).replace(/-+$/g, "");
  return slug;
}
