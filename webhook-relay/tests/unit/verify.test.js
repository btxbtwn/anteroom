import assert from "node:assert/strict";
import { createHash, createHmac, createSign, generateKeyPairSync } from "node:crypto";
import test from "node:test";

import { extractEbayEvent, extractEtsyEvent } from "../../src/extract.js";
import {
  ebayChallengeResponse,
  parseEbaySignatureHeader,
  verifyEbaySignature,
  verifyEtsySignature,
} from "../../src/verify.js";

const orderNotice = {
  metadata: { topic: "ORDER_CONFIRMATION", schemaVersion: "1.0", deprecated: false },
  notification: {
    notificationId: "abc-123",
    eventDate: "2026-10-23T20:00:00.000Z",
    publishDate: "2026-10-23T20:00:01.000Z",
    publishAttemptCount: 1,
    data: {
      orderId: "12-34567-89012",
      buyer: { username: "someone", taxAddress: { postalCode: "94110" } },
      lineItems: [{ lineItemId: "100", legacyItemId: "2950001", title: "Vintage jacket", total: "40.00" }],
    },
  },
};

test("eBay challenge matches the SDK's hash", async () => {
  const code = "a8628072-3d33-45ee-9004-bee86830a22d";
  const token = "test_".repeat(8);
  const endpoint = "https://relay.example.workers.dev/ebay";
  const expected = createHash("sha256").update(code).update(token).update(endpoint).digest("hex");
  assert.equal(await ebayChallengeResponse(code, token, endpoint), expected);
});

for (const curve of ["prime256v1", "secp384r1"]) {
  test(`eBay signature verifies (${curve}, DER, SHA-1)`, async () => {
    const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: curve });
    const pem = publicKey.export({ type: "spki", format: "pem" }).replace(/\n/g, "");
    const rawBody = JSON.stringify(orderNotice);
    const signature = createSign("sha1").update(rawBody).sign(privateKey, "base64");
    const header = Buffer.from(JSON.stringify({ alg: "ecdsa", kid: "k1", signature, digest: "SHA1" })).toString("base64");

    const parsed = parseEbaySignatureHeader(header);
    assert.equal(parsed.kid, "k1");
    assert.equal(await verifyEbaySignature({ rawBody, signature: parsed.signature, publicKeyPem: pem }), true);

    const pretty = JSON.stringify(orderNotice, null, 2);
    assert.equal(await verifyEbaySignature({ rawBody: pretty, signature, publicKeyPem: pem }), true, "re-serialised body");

    const tampered = rawBody.replace("12-34567-89012", "12-34567-89013");
    assert.equal(await verifyEbaySignature({ rawBody: tampered, signature, publicKeyPem: pem }), false);
  });
}

test("eBay signature from another key is rejected", async () => {
  const signer = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const other = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const rawBody = JSON.stringify(orderNotice);
  const signature = createSign("sha1").update(rawBody).sign(signer.privateKey, "base64");
  const pem = other.publicKey.export({ type: "spki", format: "pem" });
  assert.equal(await verifyEbaySignature({ rawBody, signature, publicKeyPem: pem }), false);
});

test("bad eBay signature headers parse to null", () => {
  assert.equal(parseEbaySignatureHeader(null), null);
  assert.equal(parseEbaySignatureHeader("not base64!"), null);
  assert.equal(parseEbaySignatureHeader(Buffer.from('{"kid":1}').toString("base64")), null);
});

function etsySign(secret, id, timestamp, body) {
  const key = Buffer.from(secret.slice(6), "base64");
  return createHmac("sha256", key).update(`${id}.${timestamp}.${body}`).digest("base64");
}

test("Etsy signature verifies and rejects tampering, wrong keys and stale timestamps", async () => {
  const secret = `whsec_${Buffer.from("0123456789abcdef0123456789abcdef").toString("base64")}`;
  const body = JSON.stringify({ event_type: "order.paid", shop_id: 42, resource_url: "https://api.etsy.com/v3/application/shops/42/receipts/9001" });
  const id = "msg_2abc";
  const now = Date.UTC(2026, 9, 23, 20, 0, 0);
  const timestamp = String(Math.floor(now / 1000));
  const sig = etsySign(secret, id, timestamp, body);
  const base = { id, timestamp, rawBody: body, secret, now };

  assert.equal(await verifyEtsySignature({ ...base, signatureHeader: `v1,${sig}` }), true);
  assert.equal(await verifyEtsySignature({ ...base, signatureHeader: `v1,bogus v1,${sig}` }), true, "rotated keys");
  assert.equal(await verifyEtsySignature({ ...base, signatureHeader: sig }), true, "unprefixed");
  assert.equal(await verifyEtsySignature({ ...base, rawBody: body.replace("42", "43"), signatureHeader: `v1,${sig}` }), false);
  assert.equal(await verifyEtsySignature({ ...base, now: now + 301_000, signatureHeader: `v1,${sig}` }), false);
  const otherSecret = `whsec_${Buffer.from("ffffffffffffffffffffffffffffffff").toString("base64")}`;
  assert.equal(await verifyEtsySignature({ ...base, secret: otherSecret, signatureHeader: `v1,${sig}` }), false);
  assert.equal(await verifyEtsySignature({ ...base, signatureHeader: null }), false);
});

test("eBay events keep IDs only", () => {
  const event = extractEbayEvent(orderNotice);
  assert.deepEqual(event, {
    source: "ebay",
    eventType: "ORDER_CONFIRMATION",
    dedupeKey: "ebay:abc-123",
    ids: { orderId: ["12-34567-89012"], lineItemId: ["100"], legacyItemId: ["2950001"] },
    occurredAt: "2026-10-23T20:00:00.000Z",
  });
  const stored = JSON.stringify(event);
  for (const secret of ["someone", "94110", "Vintage jacket", "40.00"]) assert.ok(!stored.includes(secret), secret);
});

test("eBay account deletion keeps the user ID, not the username", () => {
  const event = extractEbayEvent({
    metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
    notification: { notificationId: "n1", data: { username: "someone", userId: "ma8vp1jySJC", eiasToken: "fake-eias" } },
  });
  assert.deepEqual(event.ids, { userId: ["ma8vp1jySJC"] });
});

test("malformed eBay payloads are refused", () => {
  assert.equal(extractEbayEvent({}), null);
  assert.equal(extractEbayEvent({ metadata: { topic: "X" }, notification: {} }), null);
});

test("Etsy events keep IDs only and pull the receipt from resource_url", () => {
  assert.deepEqual(
    extractEtsyEvent({ event_type: "order.paid", shop_id: 42, resource_url: "https://api.etsy.com/v3/application/shops/42/receipts/9001" }, "msg_1"),
    { source: "etsy", eventType: "order.paid", dedupeKey: "etsy:msg_1", ids: { shop_id: ["42"], receipt_id: ["9001"] }, occurredAt: null },
  );
  assert.deepEqual(
    extractEtsyEvent({ event_type: "listing.updated", shop_id: "42", listing_id: "777" }, "msg_2").ids,
    { shop_id: ["42"], listing_id: ["777"] },
  );
  assert.equal(extractEtsyEvent({ event_type: "DROP TABLE" }, "msg_3"), null);
  assert.equal(extractEtsyEvent({ event_type: "order.paid" }, null), null);
});
