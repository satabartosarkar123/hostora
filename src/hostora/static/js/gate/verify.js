import { verifyAsync } from "/static/vendor/noble-ed25519.js";

function hexToBytes(hex) {
  if (!hex || hex.length % 2 !== 0) return null;
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    const byte = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    if (Number.isNaN(byte)) return null;
    bytes[i] = byte;
  }
  return bytes;
}

function base64UrlToBytes(b64u) {
  try {
    let base64 = b64u.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4 !== 0) {
      base64 += "=";
    }
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes;
  } catch (_err) {
    return null;
  }
}

export async function verifyPassToken(token, expectedEventKey, publicKeyHex, nowSec) {
  if (typeof token !== "string") return null;
  const parts = token.trim().split(".");
  if (parts.length !== 6 || parts[0] !== "v1") return null;
  const [, eventKey, passId, seatsStr, expStr, sigStr] = parts;
  if (eventKey !== expectedEventKey) return null;
  if (!/^\d+$/.test(seatsStr) || !/^\d+$/.test(expStr)) return null;

  const seats = parseInt(seatsStr, 10);
  const exp = parseInt(expStr, 10);
  const now = nowSec !== undefined ? nowSec : Math.floor(Date.now() / 1000);
  if (seats < 1 || exp < now) return null;

  const sigBytes = base64UrlToBytes(sigStr);
  const pubBytes = hexToBytes(publicKeyHex);
  if (!sigBytes || !pubBytes) return null;

  const payload = `v1.${eventKey}.${passId}.${seats}.${exp}`;
  const msgBytes = new TextEncoder().encode(payload);
  const valid = await verifyAsync(sigBytes, msgBytes, pubBytes);
  if (!valid) return null;

  return {
    eventKey,
    passId,
    seats,
    exp,
  };
}
