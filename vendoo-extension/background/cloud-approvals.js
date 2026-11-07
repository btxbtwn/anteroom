/* The hosted API's signed approval on each relayed write, checked before the Bridge runs it.
   Any script on the Anteroom page can hand the Bridge a command, so the page's own
   `confirm: true` is not the seller's confirmation. The hosted API signs the command
   it accepted (services/bridge_approvals.py); this checks that signature, its expiry,
   the account, command, marketplace, action and listing it names, a hash of the whole
   command, and that the approval has not been used before. */
(function () {
  // The hosted API's ES256 public key: the base64 body of the PEM that
  // `openssl ec -in bridge-signing.pem -pubout` prints, without its BEGIN/END lines.
  // Set it before releasing the Bridge (docs/hosted-production.md, **Bridge approval key**).
  // While it is empty, the Bridge refuses every write the web relays.
  const BRIDGE_APPROVAL_PUBLIC_KEY = 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAETSODemzvbkZtbvrN9kXt4ERklx7tQbbrqLkgX6QwByD/MFVX1TWzNeYgtsP5Gx0G4eLKXbCrCT1emOHbRIYq+A==';
  // Commands that change something: listings, Vendoo drafts, buyer offers. A result check
  // may resume an interrupted List. Must match SIGNED_COMMANDS on the server.
  const SIGNED_COMMANDS = new Set(['job.vendoo_api', 'marketplace.execute', 'marketplace.result',
    'marketplace.delete', 'marketplace.promote']);
  const USED_KEY = 'anteroom_used_approvals';
  // Allowed clock difference between this computer and the server.
  const LEEWAY_S = 60;
  // The longest life an approval may claim; the server issues five minutes.
  const MAX_LIFE_S = 600;
  const TOKEN = /^[A-Za-z0-9_-]{10,200}\.[A-Za-z0-9_-]{10,2000}\.[A-Za-z0-9_-]{80,90}$/;
  const REFUSALS = {
    no_key: 'This copy of Anteroom Bridge has no approval key, so it does not run marketplace actions from the web. Install the released Anteroom Bridge.',
    unsigned: 'Anteroom did not sign this action, so Chrome did not run it. Reload Anteroom and try again.',
    expired: 'This action\'s approval expired before Chrome received it. Check this computer\'s clock, then try again.',
    invalid: 'Chrome did not run this action because its approval is not valid. Reload Anteroom and try again.',
    mismatch: 'Chrome did not run this action because it is not the one Anteroom approved. Reload Anteroom and try again.',
    replayed: 'Chrome already ran this approval once. Start the action again in Anteroom.',
  };

  function bytes(text) {
    const base64 = text.replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(base64 + '='.repeat((4 - base64.length % 4) % 4)), char => char.charCodeAt(0));
  }
  function base64url(data) {
    return btoa(String.fromCharCode(...data)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  // Sorted-key JSON of the parsed command, as the server writes it before hashing.
  function canonical(value) {
    if (value === undefined) return 'null';
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
    }
    return JSON.stringify(value);
  }

  function createApprovalVerifier(api, { publicKey = BRIDGE_APPROVAL_PUBLIC_KEY, now = Date.now, subtle = crypto.subtle } = {}) {
    let key = null;
    let tail = Promise.resolve();
    async function commandHash(command) {
      const { approval: _approval, ...payload } = command.payload;
      const body = canonical({ type: command.type, job_id: command.job_id, message_id: command.message_id, payload });
      return base64url(new Uint8Array(await subtle.digest('SHA-256', new TextEncoder().encode(body))));
    }
    // Remember each approval until it expires, so a copy of it cannot run again.
    function use(id, expires) {
      const result = tail.then(async () => {
        const used = (await api.storage.local.get(USED_KEY))[USED_KEY] || {};
        const seconds = now() / 1000;
        for (const [nonce, until] of Object.entries(used)) if (until + LEEWAY_S < seconds) delete used[nonce];
        if (Object.hasOwn(used, id)) return false;
        used[id] = expires;
        await api.storage.local.set({ [USED_KEY]: used });
        return true;
      });
      tail = result.catch(() => {});
      return result;
    }
    async function check(command, accountId) {
      if (!publicKey) return 'no_key';
      const token = command.payload.approval;
      if (typeof token !== 'string') return 'unsigned';
      if (!TOKEN.test(token)) return 'invalid';
      const [head, body, signature] = token.split('.');
      let header; let claims;
      try {
        header = JSON.parse(new TextDecoder().decode(bytes(head)));
        claims = JSON.parse(new TextDecoder().decode(bytes(body)));
      } catch { return 'invalid'; }
      if (header?.alg !== 'ES256' || !claims || typeof claims !== 'object') return 'invalid';
      try {
        key ||= subtle.importKey('spki', bytes(publicKey), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
        const valid = await subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, await key, bytes(signature),
          new TextEncoder().encode(`${head}.${body}`));
        if (!valid) return 'invalid';
      } catch { key = null; return 'invalid'; }
      if (claims.iss !== 'anteroom' || claims.aud !== 'anteroom-bridge' || !Number.isInteger(claims.iat)
          || !Number.isInteger(claims.exp) || claims.exp <= claims.iat || claims.exp - claims.iat > MAX_LIFE_S
          || typeof claims.jti !== 'string' || !/^[0-9a-f]{32}$/.test(claims.jti)) return 'invalid';
      const seconds = now() / 1000;
      if (seconds > claims.exp + LEEWAY_S || claims.iat > seconds + LEEWAY_S) return 'expired';
      const p = command.payload;
      if (String(claims.sub || '').toLowerCase() !== String(accountId || '').toLowerCase() || claims.cmd !== command.type
          || claims.mkt !== (p.marketplace ?? null) || claims.act !== (p.action ?? null) || claims.lid !== (p.listing_id ?? null)
          || claims.job !== (command.job_id ?? null) || claims.mid !== command.message_id
          || claims.sha !== await commandHash(command)) return 'mismatch';
      return await use(claims.jti, claims.exp) ? null : 'replayed';
    }
    return {
      // Null when the command may run; otherwise the Bridge's refusal, which reaches the hosted API.
      async verify(command, accountId) {
        if (!SIGNED_COMMANDS.has(command?.type)) return null;
        const code = await check(command, accountId);
        if (!code) return null;
        console.warn(`[BRIDGE] Refused ${command.type}: approval ${code}`);
        return { ok: false, safe_to_retry: true, error: REFUSALS[code], error_code: 'rejected', approval: code };
      },
    };
  }
  const exported = { createApprovalVerifier, canonical, SIGNED_COMMANDS, BRIDGE_APPROVAL_PUBLIC_KEY };
  if (typeof module !== 'undefined') module.exports = exported;
  else globalThis.CloudApprovals = exported;
})();
