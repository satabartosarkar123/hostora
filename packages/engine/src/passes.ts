import * as ed from "@noble/ed25519";

export interface PassClaims {
  eventKey: string;
  passId: string;
  seats: number;
  /** unix seconds */
  exp: number;
}

function b64uEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64uDecode(str: string): Uint8Array {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const s = atob(padded);
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

export function passPayload(c: PassClaims): string {
  return `v1.${c.eventKey}.${c.passId}.${c.seats}.${c.exp}`;
}

export async function signPass(claims: PassClaims, privateKeyHex: string): Promise<string> {
  if (/[.]/.test(claims.passId) || /[.]/.test(claims.eventKey)) {
    throw new Error("eventKey and passId must not contain dots");
  }
  const msg = new TextEncoder().encode(passPayload(claims));
  const sig = await ed.signAsync(msg, ed.etc.hexToBytes(privateKeyHex));
  return `${passPayload(claims)}.${b64uEncode(sig)}`;
}

/** Verifies offline. Returns the claims, or null if malformed, expired or forged. */
export async function verifyPass(
  token: string,
  publicKeyHex: string,
  nowSec: number,
): Promise<PassClaims | null> {
  const parts = token.split(".");
  if (parts.length !== 6 || parts[0] !== "v1") return null;
  const [, eventKey, passId, seatsS, expS, sigS] = parts as [string, string, string, string, string, string];
  const seats = Number(seatsS);
  const exp = Number(expS);
  if (!Number.isInteger(seats) || seats < 1 || !Number.isInteger(exp) || exp < nowSec) return null;
  const claims: PassClaims = { eventKey, passId, seats, exp };
  try {
    const ok = await ed.verifyAsync(
      b64uDecode(sigS),
      new TextEncoder().encode(passPayload(claims)),
      ed.etc.hexToBytes(publicKeyHex),
    );
    return ok ? claims : null;
  } catch {
    return null;
  }
}

export async function generateKeyPairHex(): Promise<{ privateKeyHex: string; publicKeyHex: string }> {
  const priv = ed.utils.randomPrivateKey();
  const pub = await ed.getPublicKeyAsync(priv);
  return { privateKeyHex: ed.etc.bytesToHex(priv), publicKeyHex: ed.etc.bytesToHex(pub) };
}
