// Checks that a webhook really came from eBay or Etsy. Pure Web Crypto, so the
// same code runs in the Worker and under `node --test`.

const encoder = new TextEncoder();

export function base64ToBytes(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function bytesToBase64(bytes) {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function timingSafeEqual(a, b) {
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  let diff = left.length ^ right.length;
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return diff === 0;
}

// eBay's GET challenge: hex SHA-256 of challengeCode + verificationToken +
// endpoint, in that order, where endpoint is the exact URL saved in eBay's portal.
export async function ebayChallengeResponse(challengeCode, verificationToken, endpoint) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(challengeCode + verificationToken + endpoint),
  );
  return bytesToHex(digest);
}

// X-EBAY-SIGNATURE is base64 JSON: {alg, kid, signature, digest}.
export function parseEbaySignatureHeader(header) {
  if (!header) return null;
  try {
    const parsed = JSON.parse(new TextDecoder().decode(base64ToBytes(header)));
    if (typeof parsed?.kid !== "string" || typeof parsed?.signature !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

// eBay signs with DER-encoded ECDSA; Web Crypto wants raw r||s.
export function derToRaw(der, size) {
  let offset = 0;
  if (der[offset++] !== 0x30) throw new Error("not a DER sequence");
  let length = der[offset++];
  if (length & 0x80) offset += length & 0x7f;
  const parts = [];
  for (let n = 0; n < 2; n += 1) {
    if (der[offset++] !== 0x02) throw new Error("not a DER integer");
    length = der[offset++];
    let value = der.slice(offset, offset + length);
    offset += length;
    while (value.length > size && value[0] === 0) value = value.slice(1);
    if (value.length > size) throw new Error("integer too long");
    const padded = new Uint8Array(size);
    padded.set(value, size - value.length);
    parts.push(padded);
  }
  const raw = new Uint8Array(size * 2);
  raw.set(parts[0], 0);
  raw.set(parts[1], size);
  return raw;
}

function pemToSpki(pem) {
  const body = pem
    .replace(/-----BEGIN PUBLIC KEY-----/, "")
    .replace(/-----END PUBLIC KEY-----/, "")
    .replace(/\s+/g, "");
  return base64ToBytes(body);
}

const CURVES = [
  { namedCurve: "P-256", size: 32 },
  { namedCurve: "P-384", size: 48 },
];

// eBay's SDK verifies SHA-1 ECDSA over JSON.stringify(parsed body). Try the raw
// bytes first, then that re-serialisation, so either form passes.
export async function verifyEbaySignature({ rawBody, signature, publicKeyPem }) {
  const spki = pemToSpki(publicKeyPem);
  const der = base64ToBytes(signature);
  const candidates = [rawBody];
  try {
    const compact = JSON.stringify(JSON.parse(rawBody));
    if (compact !== rawBody) candidates.push(compact);
  } catch {
    return false;
  }
  for (const curve of CURVES) {
    let key;
    try {
      key = await crypto.subtle.importKey(
        "spki",
        spki,
        { name: "ECDSA", namedCurve: curve.namedCurve },
        false,
        ["verify"],
      );
    } catch {
      continue;
    }
    let raw;
    try {
      raw = derToRaw(der, curve.size);
    } catch {
      return false;
    }
    for (const message of candidates) {
      const ok = await crypto.subtle.verify(
        { name: "ECDSA", hash: "SHA-1" },
        key,
        raw,
        encoder.encode(message),
      );
      if (ok) return true;
    }
    return false;
  }
  return false;
}

export const ETSY_TOLERANCE_SECONDS = 300;

// Etsy follows Standard Webhooks: HMAC-SHA256 over "id.timestamp.body" with the
// base64 key after the whsec_ prefix; the header holds space-separated "v1,<sig>".
export async function verifyEtsySignature({ id, timestamp, signatureHeader, rawBody, secret, now }) {
  if (!id || !timestamp || !signatureHeader || !secret) return false;
  const seconds = Number(timestamp);
  if (!Number.isFinite(seconds)) return false;
  const current = Math.floor((now ?? Date.now()) / 1000);
  if (Math.abs(current - seconds) > ETSY_TOLERANCE_SECONDS) return false;
  const keyBytes = base64ToBytes(secret.startsWith("whsec_") ? secret.slice(6) : secret);
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(`${id}.${timestamp}.${rawBody}`));
  const expected = bytesToBase64(mac);
  return signatureHeader
    .split(" ")
    .map((entry) => (entry.includes(",") ? entry.slice(entry.indexOf(",") + 1) : entry))
    .some((candidate) => timingSafeEqual(candidate, expected));
}

function base64Url(bytes) {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomToken(bytes = 32) {
  return base64Url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function sha256Hex(value) {
  return bytesToHex(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

// PKCE S256: base64url(SHA-256(verifier)).
export async function pkceChallenge(verifier) {
  return base64Url(await crypto.subtle.digest("SHA-256", encoder.encode(verifier)));
}
