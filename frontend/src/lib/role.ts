// Turns a verified Supabase sign-in into one of our roles.

import type { Role } from "@/lib/access";

export interface Claims {
  sub?: string;
  email?: string;
  is_anonymous?: boolean;
}

/**
 * The broker is the one account whose email is BROKER_EMAIL. Anyone can type that address into the sign-up form, so the address
 * only counts once Supabase has confirmed it belongs to the account (`emailConfirmed`, read from the auth server, not from the token).
 */
export function roleFromClaims(claims: Claims | null | undefined, brokerEmail: string, emailConfirmed: boolean): Role {
  if (!claims?.sub || claims.is_anonymous) return "anonymous";
  const broker = brokerEmail.trim().toLowerCase();
  if (broker && emailConfirmed && claims.email?.trim().toLowerCase() === broker) return "broker";
  return "buyer";
}

/** Does this account need the auth server's word that its email is confirmed? Only the one that would become the broker does. */
export function mightBeBroker(claims: Claims | null | undefined, brokerEmail: string): boolean {
  const broker = brokerEmail.trim().toLowerCase();
  return Boolean(broker && claims?.email?.trim().toLowerCase() === broker);
}
