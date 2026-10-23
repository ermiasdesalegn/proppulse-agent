// Login cookie: "<expiry-ms>.<hmac>". Uses Web Crypto only, so the same code runs in middleware (edge) and route handlers.

const encoder = new TextEncoder();

function toHex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function sign(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return toHex(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}

/** Compare without stopping at the first difference, so timing reveals nothing about how much matched. */
export function safeEqual(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export async function passwordMatches(input: string, expected: string): Promise<boolean> {
  if (!expected) return false; // an unset password must never match an empty input
  const [a, b] = await Promise.all([crypto.subtle.digest("SHA-256", encoder.encode(input)), crypto.subtle.digest("SHA-256", encoder.encode(expected))]);
  return safeEqual(toHex(a), toHex(b));
}

export async function createSession(secret: string, ttlSeconds: number, now = Date.now()): Promise<string> {
  const expires = String(now + ttlSeconds * 1000);
  return `${expires}.${await sign(secret, expires)}`;
}

export async function verifySession(token: string | undefined, secret: string, now = Date.now()): Promise<boolean> {
  if (!token || !secret) return false;
  const [expires, signature, extra] = token.split(".");
  if (!expires || !signature || extra !== undefined || !/^\d+$/.test(expires)) return false;
  if (Number(expires) <= now) return false;
  return safeEqual(signature, await sign(secret, expires));
}

export const SESSION_COOKIE = "pp_session";
export const SESSION_TTL_SECONDS = 12 * 60 * 60;
