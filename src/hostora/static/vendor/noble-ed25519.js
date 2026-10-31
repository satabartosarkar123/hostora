// Vendored pure-JS Ed25519 signature verification (RFC 8032) + WebCrypto compatibility
// Exposes verifyAsync(signatureBytes, messageBytes, publicKeyBytes) -> Promise<boolean>

const P = 2n ** 255n - 19n;
const L = 2n ** 252n + 27742317777372353535851937790883648493n;

function mod(a, m = P) {
  const r = a % m;
  return r >= 0n ? r : r + m;
}

function modPow(base, exp, m = P) {
  let res = 1n;
  let b = mod(base, m);
  let e = exp;
  while (e > 0n) {
    if (e & 1n) res = (res * b) % m;
    b = (b * b) % m;
    e >>= 1n;
  }
  return res;
}

function inv(a) {
  return modPow(a, P - 2n, P);
}

const D = mod(-121665n * inv(121666n));
const I = modPow(2n, (P - 1n) / 4n);

const Bx = 15112221349535400772501151409588531511454012693041857206046113283949847762202n;
const By = 46316835694926478169428394003475163141307993866256225615783033603165251855960n;
const B = [Bx, By, 1n, mod(Bx * By)];
const ZERO = [0n, 1n, 1n, 0n];

function pointAdd(P1, P2) {
  const [X1, Y1, Z1, T1] = P1;
  const [X2, Y2, Z2, T2] = P2;
  const A = mod((Y1 - X1) * (Y2 - X2));
  const Bv = mod((Y1 + X1) * (Y2 + X2));
  const C = mod(T1 * 2n * D * T2);
  const Dv = mod(Z1 * 2n * Z2);
  const E = mod(Bv - A);
  const F = mod(Dv - C);
  const G = mod(Dv + C);
  const H = mod(Bv + A);
  return [mod(E * F), mod(G * H), mod(F * G), mod(E * H)];
}

function scalarMult(Pt, n) {
  let R = ZERO;
  let Q = Pt;
  let k = n;
  while (k > 0n) {
    if (k & 1n) R = pointAdd(R, Q);
    Q = pointAdd(Q, Q);
    k >>= 1n;
  }
  return R;
}

function bytesToNumberLE(bytes) {
  let v = 0n;
  for (let i = 0; i < bytes.length; i++) {
    v += BigInt(bytes[i]) << (8n * BigInt(i));
  }
  return v;
}

function encodePoint(Pt) {
  const [X, Y, Z] = Pt;
  const zi = inv(Z);
  const x = mod(X * zi);
  const y = mod(Y * zi);
  const out = new Uint8Array(32);
  let v = y;
  for (let i = 0; i < 32; i++) {
    out[i] = Number(v & 0xffn);
    v >>= 8n;
  }
  out[31] |= Number(x & 1n) << 7;
  return out;
}

function decodePoint(bytes) {
  if (bytes.length !== 32) return null;
  const copy = new Uint8Array(bytes);
  const x0 = (copy[31] >> 7) & 1;
  copy[31] &= 0x7f;
  const y = bytesToNumberLE(copy);
  if (y >= P) return null;
  const y2 = mod(y * y);
  const u = mod(y2 - 1n);
  const v = mod(D * y2 + 1n);
  const v3 = mod(v * v * v);
  const v7 = mod(v3 * v3 * v);
  let x = mod(u * v3 * modPow(u * v7, (P - 5n) / 8n));
  const vx2 = mod(v * x * x);
  if (vx2 === u) {
    // ok
  } else if (vx2 === mod(-u)) {
    x = mod(x * I);
  } else {
    return null;
  }
  if (x === 0n && x0 === 1) return null;
  if (Number(x & 1n) !== x0) x = P - x;
  return [x, y, 1n, mod(x * y)];
}

function equalBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function verifyAsync(signature, message, publicKey) {
  try {
    const sig = signature instanceof Uint8Array ? signature : new Uint8Array(signature);
    const msg = message instanceof Uint8Array ? message : new Uint8Array(message);
    const pub = publicKey instanceof Uint8Array ? publicKey : new Uint8Array(publicKey);
    if (sig.length !== 64 || pub.length !== 32) return false;

    const Rbytes = sig.slice(0, 32);
    const Sbytes = sig.slice(32, 64);
    const S = bytesToNumberLE(Sbytes);
    if (S >= L) return false;

    const R = decodePoint(Rbytes);
    const A = decodePoint(pub);
    if (!R || !A) return false;

    const combined = new Uint8Array(64 + msg.length);
    combined.set(Rbytes, 0);
    combined.set(pub, 32);
    combined.set(msg, 64);

    const hashBuf = await crypto.subtle.digest("SHA-512", combined);
    const k = bytesToNumberLE(new Uint8Array(hashBuf)) % L;

    const lhs = encodePoint(scalarMult(B, S));
    const rhs = encodePoint(pointAdd(R, scalarMult(A, k)));
    return equalBytes(lhs, rhs);
  } catch (_err) {
    return false;
  }
}

export const verify = verifyAsync;
export default { verifyAsync, verify };
