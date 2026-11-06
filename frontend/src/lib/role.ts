// Turns a verified Supabase sign-in, plus the organizations the person belongs to, into one of our roles.

import type { Role } from "@/lib/access";

export interface Claims {
  sub?: string;
  email?: string;
  is_anonymous?: boolean;
}

/**
 * "broker" is the console role: a signed-in person who belongs to at least one organization (the backend's `memberships` table
 * decides that, and what they may do inside it). Nobody becomes one by typing a particular email address into the sign-up form:
 * membership is granted by an owner's invitation or by an operator (`python -m scripts.grant_role`).
 */
export function roleFromClaims(claims: Claims | null | undefined, organizationCount: number): Role {
  if (!claims?.sub || claims.is_anonymous) return "anonymous";
  return organizationCount > 0 ? "broker" : "buyer";
}
