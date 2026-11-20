// A test-only stand-in for the hosted API's approval signer (services/bridge_approvals.py):
// a P-256 key made for this run, and ES256 tokens over the same claims and command hash.
const { canonical } = require('../background/cloud-approvals');

const encode = value => Buffer.from(value).toString('base64url');

async function approvalSigner() {
  const keys = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const publicKey = Buffer.from(await crypto.subtle.exportKey('spki', keys.publicKey)).toString('base64');
  async function commandHash(command) {
    const { approval: _approval, ...payload } = command.payload;
    const body = canonical({ type: command.type, job_id: command.job_id, message_id: command.message_id, payload });
    return encode(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body))));
  }
  async function token(claims, privateKey = keys.privateKey) {
    const head = encode(JSON.stringify({ alg: 'ES256', typ: 'JWT' }));
    const body = encode(JSON.stringify(claims));
    const signature = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, privateKey, new TextEncoder().encode(`${head}.${body}`));
    return `${head}.${body}.${encode(new Uint8Array(signature))}`;
  }
  // `command` with the approval the server would add for `accountId` at `seconds`, binding the
  // SHA-256 of each relayed `photos` entry, after `changes` to its claims.
  async function sign(command, accountId, { seconds = Math.floor(Date.now() / 1000), changes = {}, privateKey, photos = [] } = {}) {
    const p = command.payload;
    const pho = photos.map(photo => ({ id: photo.id,
      sha256: require('node:crypto').createHash('sha256').update(Buffer.from(photo.base64, 'base64')).digest('hex') }));
    const claims = { iss: 'anteroom', aud: 'anteroom-bridge', sub: accountId, cmd: command.type, mkt: p.marketplace ?? null,
      act: p.action ?? null, lid: p.listing_id ?? null, job: command.job_id ?? null, mid: command.message_id,
      sha: await commandHash(command), jti: crypto.randomUUID().replace(/-/g, ''), iat: seconds, exp: seconds + 300, pho, ...changes };
    return { ...command, payload: { ...p, approval: await token(claims, privateKey) } };
  }
  return { publicKey, sign };
}

module.exports = { approvalSigner };
