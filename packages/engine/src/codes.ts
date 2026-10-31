/** Crockford base32 alphabet: no I, L, O or U, so codes survive being read aloud. */
export const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

export function randomKey(length = 5): string {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  let out = "";
  for (const b of bytes) out += ALPHABET.charAt(b & 31);
  return out;
}

/** Normalise a typed code: upper-case, drop spaces and dashes, map look-alikes. Null if invalid. */
export function normalizeCode(input: string): string | null {
  const cleaned = input.toUpperCase().replace(/[\s-]/g, "").replace(/O/g, "0").replace(/[IL]/g, "1");
  if (cleaned.length === 0 || !/^[0-9A-HJKMNP-TV-Z]+$/.test(cleaned)) return null;
  return cleaned;
}

/** Typed household code = 5-char event key + 5-char household key, shown as XXXXX-XXXXX. */
export function splitHouseholdCode(input: string): { eventKey: string; householdKey: string } | null {
  const code = normalizeCode(input);
  if (!code || code.length !== 10) return null;
  return { eventKey: code.slice(0, 5), householdKey: code.slice(5) };
}

export function formatHouseholdCode(eventKey: string, householdKey: string): string {
  return `${eventKey}-${householdKey}`;
}

async function hmacBase32(secret: string, message: string, chars: number): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
  let out = "";
  for (let i = 0; i < chars; i++) out += ALPHABET.charAt((sig[i] ?? 0) & 31);
  return out;
}

/** 16-char link token = householdKey (5) + HMAC tag (11, 55 bits). */
export async function makeLinkToken(secret: string, eventKey: string, householdKey: string): Promise<string> {
  return householdKey + (await hmacBase32(secret, `${eventKey}.${householdKey}`, 11));
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Returns the household key when the token is valid for this event, otherwise null. */
export async function verifyLinkToken(
  secret: string,
  eventKey: string,
  token: string,
): Promise<string | null> {
  if (token.length !== 16) return null;
  const householdKey = token.slice(0, 5);
  const expected = await makeLinkToken(secret, eventKey, householdKey);
  return timingSafeEqual(expected, token) ? householdKey : null;
}
