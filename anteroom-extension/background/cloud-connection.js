/* Browser-local site permission, form reads, relayed commands, and opt-in marketplace Cloud session upload. */
(function () {
  const ORIGIN = 'https://myanteroom.com';
  const KEY = 'anteroom_cloud_connections';
  const APPROVALS_KEY = 'anteroom_cloud_approvals';
  const CLOUD_SESSION_KEY = 'anteroom_cloud_session_connects';
  const TTL = 2 * 60 * 1000;
  const CLOUD_SESSION_TTL = 3 * 60 * 1000;
  // A remembered approval lasts this long; then the seller approves the account again.
  const APPROVAL_TTL = 30 * 24 * 60 * 60 * 1000;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const UPLOAD_TOKEN_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_-]{20,200}$/i;
  const CLOUD_SESSION_MARKETS = new Set(['poshmark', 'mercari', 'depop']);
  const TOKEN_COOKIES = { poshmark: 'jwt', depop: 'access_token' };
  const PAGE_ACTIONS = new Set(['status', 'connect', 'disconnect']);
  const APPROVAL_ACTIONS = new Set(['details', 'approve', 'deny']);
  const CLOUD_SESSION_APPROVAL_ACTIONS = new Set(['cloud_session_details', 'cloud_session_approve', 'cloud_session_deny']);
  // What a relaying Bridge runs: marketplace actions, and each named read under its own name,
  // and how it checks them: a signed approval on every relayed command, binding the photos
  // relayed with it (background/cloud-approvals.js). It no longer declares the writes-only
  // `marketplace.signed_approvals` of Bridges 0.2.56 to 0.2.67, which the server drops later.
  const RELAY_CAPABILITIES = ['marketplace.actions', 'marketplace.sessions', 'marketplace.sales_history', 'marketplace.promotions', 'marketplace.import', 'vendoo.drafts', 'marketplace.signed_commands', 'marketplace.cloud_connect'];

  function isMarketplaceCookieDomain(domain, root) {
    const clean = String(domain || '').trim().toLowerCase().replace(/^\./, '');
    return clean === root || clean.endsWith(`.${root}`);
  }

  async function defaultUploadCloudSession(api, { marketplace, uploadToken }) {
    if (!CLOUD_SESSION_MARKETS.has(marketplace)) return { ok: false, error: 'Unsupported cloud marketplace.' };
    if (!api.cookies?.getAll) return { ok: false, error: 'Chrome cookies API is unavailable.' };
    const domain = `${marketplace}.com`;
    const rawCookies = await api.cookies.getAll({ domain });
    const cookies = (Array.isArray(rawCookies) ? rawCookies : [])
      .filter(cookie => cookie && typeof cookie.name === 'string' && typeof cookie.value === 'string' && isMarketplaceCookieDomain(cookie.domain, domain))
      .map(cookie => ({
        name: cookie.name,
        value: cookie.value,
        domain: cookie.domain,
        path: cookie.path || '/',
        secure: Boolean(cookie.secure),
        httpOnly: Boolean(cookie.httpOnly),
        sameSite: cookie.sameSite || 'unspecified',
        ...(Number.isFinite(cookie.expirationDate) ? { expirationDate: cookie.expirationDate } : {}),
      }));
    const tokenCookie = TOKEN_COOKIES[marketplace];
    if (!cookies.some(cookie => cookie.value && (!tokenCookie || cookie.name === tokenCookie))) {
      const name = marketplace[0].toUpperCase() + marketplace.slice(1);
      return { ok: false, error: `Sign in to ${name} in Chrome first, then try again.` };
    }
    const userAgent = typeof navigator !== 'undefined' && typeof navigator.userAgent === 'string' ? navigator.userAgent : '';
    const response = await fetch(`${ORIGIN}/api/cloud-sessions/${marketplace}/upload`, {
      method: 'POST',
      credentials: 'omit',
      redirect: 'error',
      signal: AbortSignal.timeout(55000),
      headers: {
        'Content-Type': 'application/json',
        'X-Cloud-Upload-Token': uploadToken,
      },
      body: JSON.stringify({ cookies, user_agent: userAgent }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || data?.ok !== true) {
      return { ok: false, error: typeof data?.detail === 'string' ? data.detail : `Upload failed (HTTP ${response.status}).` };
    }
    return { ok: true };
  }

  function createCloudConnection(api, { now = Date.now, randomId = () => crypto.randomUUID(), readForm = null, runCommand = null,
    verifyApproval = null, uploadCloudSession = null } = {}) {
    // A Bridge that runs relayed commands must check the hosted API's signature on each one.
    if (runCommand && !verifyApproval) throw new Error('Relayed commands need an approval check.');
    const performCloudUpload = uploadCloudSession || (opts => defaultUploadCloudSession(api, opts));
    // Serializing state changes prevents two tabs from overwriting each other's
    // session records across asynchronous chrome.storage operations.
    let tail = Promise.resolve();
    const serial = task => {
      const result = tail.then(task);
      tail = result.catch(() => {});
      return result;
    };
    const site = value => { try { return new URL(value).origin === ORIGIN; } catch { return false; } };
    const popup = sender => sender.id === api.runtime.id && typeof sender.url === 'string'
      && sender.url.split('#')[0] === api.runtime.getURL('cloud-connect.html');
    const sessionPopup = sender => sender.id === api.runtime.id && typeof sender.url === 'string'
      && sender.url.split('#')[0] === api.runtime.getURL('cloud-session-connect.html');
    const page = sender => sender.id === api.runtime.id && Number.isInteger(sender.tab?.id)
      && sender.frameId === 0 && typeof sender.documentId === 'string'
      && site(sender.url) && site(sender.tab.url) && (!sender.origin || sender.origin === ORIGIN);
    const status = state => ({ ok: true, state, extensionVersion: api.runtime.getManifest().version, supportedActions: [...(readForm ? ['poshmark.form.read'] : []), ...(runCommand ? RELAY_CAPABILITIES : [])] });
    async function read() { return (await api.storage.session.get(KEY))[KEY] || {}; }
    async function write(records) { await api.storage.session.set({ [KEY]: records }); }
    async function readCloudSessions() { return (await api.storage.session.get(CLOUD_SESSION_KEY))[CLOUD_SESSION_KEY] || {}; }
    async function writeCloudSessions(records) { await api.storage.session.set({ [CLOUD_SESSION_KEY]: records }); }
    async function readApprovals() { return (await api.storage.local.get(APPROVALS_KEY))[APPROVALS_KEY] || {}; }
    async function writeApprovals(records) { await api.storage.local.set({ [APPROVALS_KEY]: records }); }
    // An approval from before approvals expired has no time and counts as expired.
    const live = approval => UUID.test(approval?.connectionId || '') && Number.isFinite(approval.approvedAt)
      && now() - approval.approvedAt < APPROVAL_TTL && approval.approvedAt <= now();
    async function closeApproval(record) {
      if (Number.isInteger(record?.pending?.windowId)) {
        try { await api.windows.remove(record.pending.windowId); } catch { /* Already closed. */ }
      }
    }
    async function closeWindow(windowId) {
      if (Number.isInteger(windowId)) {
        try { await api.windows.remove(windowId); } catch { /* Already closed. */ }
      }
    }
    async function clearCloudSessionsForTab(tabId) {
      const pending = await readCloudSessions();
      let changed = false;
      for (const [reqId, item] of Object.entries(pending)) {
        if (item.tabId !== Number(tabId)) continue;
        delete pending[reqId];
        changed = true;
        await closeWindow(item.windowId);
      }
      if (changed) await writeCloudSessions(pending);
    }
    async function clear(tabId) {
      const records = await read();
      const record = records[tabId];
      delete records[tabId];
      await write(records);
      await closeApproval(record);
      await clearCloudSessionsForTab(tabId);
    }
    async function disconnect(accountId) {
      const approvals = await readApprovals();
      delete approvals[accountId];
      await writeApprovals(approvals);
      const records = await read();
      for (const [id, record] of Object.entries(records)) {
        if (record.accountId !== accountId) continue;
        delete records[id];
        await closeApproval(record);
      }
      await write(records);
      const pending = await readCloudSessions();
      let changed = false;
      for (const [reqId, item] of Object.entries(pending)) {
        if (item.accountId !== accountId) continue;
        delete pending[reqId];
        changed = true;
        await closeWindow(item.windowId);
      }
      if (changed) await writeCloudSessions(pending);
    }
    async function handleCloudSessionConnect(message, sender) {
      const allowed = ['type', 'action', 'accountId', 'email', 'marketplace', 'uploadToken'];
      if (!page(sender) || !UUID.test(message.accountId || '')
          || Object.keys(message).some(key => !allowed.includes(key))
          || typeof message.email !== 'string' || message.email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(message.email)
          || !CLOUD_SESSION_MARKETS.has(message.marketplace)
          || typeof message.uploadToken !== 'string' || !UPLOAD_TOKEN_RE.test(message.uploadToken)
          || message.uploadToken.slice(0, 36).toLowerCase() !== message.accountId.toLowerCase()) {
        return { ok: false, state: 'unavailable', error: 'Invalid cloud connect request.' };
      }
      const pending = await readCloudSessions();
      const currentTime = now();
      for (const [reqId, item] of Object.entries(pending)) {
        if (currentTime - item.createdAt >= CLOUD_SESSION_TTL || (item.tabId === sender.tab.id && item.marketplace === message.marketplace)) {
          delete pending[reqId];
          await closeWindow(item.windowId);
        }
      }
      const requestId = randomId();
      const entry = {
        requestId,
        tabId: sender.tab.id,
        documentId: sender.documentId,
        accountId: message.accountId,
        email: message.email,
        marketplace: message.marketplace,
        uploadToken: message.uploadToken,
        createdAt: currentTime,
      };
      pending[requestId] = entry;
      await writeCloudSessions(pending);
      try {
        const win = await api.windows.create({
          url: api.runtime.getURL('cloud-session-connect.html') + '#' + requestId,
          type: 'popup',
          width: 460,
          height: 460,
          focused: true,
        });
        entry.windowId = win.id;
        await writeCloudSessions(pending);
      } catch {
        delete pending[requestId];
        await writeCloudSessions(pending);
        return { ok: false, state: 'unavailable', error: 'Could not open confirmation window.' };
      }
      return { ok: true, state: 'pending' };
    }
    async function handleCloudSessionApproval(message, sender) {
      if (!sessionPopup(sender)
          || Object.keys(message).some(key => !['type', 'action', 'requestId'].includes(key))
          || !UUID.test(message.requestId || '')) {
        return { ok: false };
      }
      const pending = await readCloudSessions();
      const entry = pending[message.requestId];
      if (!entry) return { ok: false };
      if (now() - entry.createdAt >= CLOUD_SESSION_TTL) {
        delete pending[message.requestId];
        await writeCloudSessions(pending);
        return { ok: false, error: 'This request expired. Return to Anteroom Settings and try again.' };
      }
      if (message.action === 'cloud_session_details') {
        return { ok: true, email: entry.email, marketplace: entry.marketplace };
      }
      delete pending[message.requestId];
      await writeCloudSessions(pending);
      if (message.action === 'cloud_session_deny') return { ok: true };
      try {
        const tab = await api.tabs.get(Number(entry.tabId));
        if (!site(tab.url)) throw new Error('Tab moved');
        const reply = await api.tabs.sendMessage(
          Number(entry.tabId),
          { type: 'cloud.connection.probe', accountId: entry.accountId },
          { documentId: entry.documentId },
        );
        if (reply?.current !== true) throw new Error('Account changed');
      } catch {
        return { ok: false, error: 'The page or account changed. Return to Anteroom Settings and try again.' };
      }
      return () => performCloudUpload({ marketplace: entry.marketplace, uploadToken: entry.uploadToken });
    }
    async function handle(message, sender) {
      if (!message || typeof message !== 'object' || message.type !== 'cloud.connection') return undefined;
      if (message.action === 'cloud_session_connect') return handleCloudSessionConnect(message, sender);
      if (CLOUD_SESSION_APPROVAL_ACTIONS.has(message.action)) return handleCloudSessionApproval(message, sender);
      if (PAGE_ACTIONS.has(message.action)) {
        const allowed = ['type', 'action', 'accountId', ...(message.action === 'connect' ? ['email'] : [])];
        if (!page(sender) || !UUID.test(message.accountId || '') || Object.keys(message).some(key => !allowed.includes(key))) return { ok: false };
        if (message.action === 'connect' && (typeof message.email !== 'string' || message.email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(message.email))) return { ok: false };
        const records = await read();
        const id = sender.tab.id;
        let record = records[id];
        if (message.action === 'disconnect') {
          await disconnect(message.accountId);
          return status('disconnected');
        }
        // Identity labels are supplied by the signed-in page, not an API
        // credential. Never revive a different account's connection in this tab.
        if (record && record.accountId !== message.accountId) {
          await closeApproval(record);
          delete records[id];
          record = null;
          await write(records);
        }
        if (record?.pending && now() - record.pending.createdAt >= TTL) {
          await closeApproval(record);
          delete records[id];
          record = null;
          await write(records);
        }
        const approvals = await readApprovals();
        const approval = approvals[message.accountId];
        if (approval && !live(approval)) {
          // Expired: forget it, so Connect asks the seller in the extension's window again.
          delete approvals[message.accountId];
          await writeApprovals(approvals);
        }
        if (live(approval)) {
          await closeApproval(record);
          records[id] = { accountId: message.accountId, approved: true, connectionId: approval.connectionId };
          await write(records);
          return status('connected');
        }
        if (record?.approved) {
          delete records[id];
          record = null;
          await write(records);
        }
        if (record?.pending) return status('pending');
        if (message.action === 'status') return status('disconnected');
        const requestId = randomId();
        const pending = { requestId, documentId: sender.documentId, createdAt: now(), email: message.email };
        records[id] = { accountId: message.accountId, pending };
        await write(records);
        try {
          const window = await api.windows.create({ url: api.runtime.getURL('cloud-connect.html') + '#' + requestId,
            type: 'popup', width: 460, height: 440, focused: true });
          pending.windowId = window.id;
          await write(records);
        } catch {
          delete records[id]; await write(records); return { ok: false };
        }
        return status('pending');
      }
      if (!APPROVAL_ACTIONS.has(message.action) || !popup(sender)
          || Object.keys(message).some(key => !['type', 'action', 'requestId'].includes(key)) || !UUID.test(message.requestId || '')) return { ok: false };
      const records = await read();
      const entry = Object.entries(records).find(([, record]) => record.pending?.requestId === message.requestId);
      if (!entry) return { ok: false };
      const [id, record] = entry;
      if (now() - record.pending.createdAt >= TTL) { await clear(id); return { ok: false }; }
      if (message.action === 'details') return { ok: true, email: record.pending.email };
      if (message.action === 'deny') { await clear(id); return { ok: true }; }
      try {
        const tab = await api.tabs.get(Number(id));
        if (!site(tab.url)) throw new Error('Tab moved');
        // Target the exact document that requested approval. Reloading, closing
        // or signing out before approving invalidates the outstanding request.
        const reply = await api.tabs.sendMessage(Number(id), { type: 'cloud.connection.probe', accountId: record.accountId },
          { documentId: record.pending.documentId });
        if (reply?.current !== true) throw new Error('Account changed');
      } catch { await clear(id); return { ok: false }; }
      const connectionId = randomId();
      const approvals = await readApprovals();
      approvals[record.accountId] = { connectionId, approvedAt: now() };
      await writeApprovals(approvals);
      records[id] = { accountId: record.accountId, approved: true, connectionId };
      await write(records);
      return { ok: true };
    }
    // A checker for the page's approved connection, document and account, or null when it has none.
    async function approvedPage(message, sender) {
      const granted = async () => {
        const record = (await read())[sender.tab.id];
        const approval = (await readApprovals())[message.accountId];
        return record?.approved && record.accountId === message.accountId && live(approval)
          && approval.connectionId === record.connectionId ? record.connectionId : null;
      };
      const connection = await serial(granted);
      if (!connection) return null;
      return async function current() {
        if (await serial(granted) !== connection) return false;
        try {
          const tab = await api.tabs.get(sender.tab.id);
          if (!site(tab.url)) return false;
          const reply = await api.tabs.sendMessage(sender.tab.id, { type: 'cloud.connection.probe', accountId: message.accountId }, { documentId: sender.documentId });
          if (reply?.current !== true) return false;
          return await serial(granted) === connection;
        } catch { return false; }
      };
    }
    async function readMarketplace(message, sender) {
      if (!readForm || !page(sender) || !UUID.test(message.accountId || '')
          || Object.keys(message).some(key => !['type', 'action', 'accountId', 'listing'].includes(key))) return { ok: false, error_code: 'not_connected' };
      const listing = CloudMarketplaceProtocol.listing(message.listing);
      if (!listing) return { ok: false, error_code: 'invalid_input' };
      const current = await approvedPage(message, sender);
      if (!current) return { ok: false, error_code: 'not_connected' };
      if (!await current()) return { ok: false, error_code: 'account_changed' };
      // Catalog I/O stays outside the state queue so sign-out can clear the
      // permission immediately. Recheck both connection and document afterwards.
      const result = await readForm(listing);
      if (!await current()) return { ok: false, error_code: 'account_changed' };
      return result;
    }
    async function relayCommand(message, sender) {
      if (!runCommand || !page(sender) || !UUID.test(message.accountId || '')
          || Object.keys(message).some(key => !['type', 'action', 'accountId', 'command', 'photos'].includes(key))) return { ok: false, error_code: 'not_connected' };
      const command = CloudMarketplaceProtocol.command(message.command);
      const photos = CloudMarketplaceProtocol.photos(message.photos);
      if (!command || !photos) return { ok: false, error_code: 'invalid_input' };
      const current = await approvedPage(message, sender);
      if (!current) return { ok: false, error_code: 'not_connected' };
      if (!await current()) return { ok: false, error_code: 'account_changed' };
      // A command runs only with the hosted API's signed approval for this account,
      // exactly this command and exactly these photos. The refusal goes back to the
      // hosted API, which shows it.
      const refusal = await verifyApproval(command, message.accountId, photos);
      if (refusal) return { ok: true, reply: refusal };
      const { approval: _approval, ...payload } = command.payload;
      // The marketplace call runs outside the state queue, like a form read. Its
      // reply goes back to the hosted API even if the page then signs out, so
      // a finished List is never mistaken for one that did not happen.
      return { ok: true, reply: await runCommand({ ...command, payload }, photos) };
    }
    return {
      handle: (message, sender) => message?.type === 'cloud.connection' && message.action === 'poshmark_form'
        ? readMarketplace(message, sender)
        : message?.type === 'cloud.connection' && message.action === 'marketplace_command'
          ? relayCommand(message, sender) : serial(() => handle(message, sender)).then(result => typeof result === 'function' ? result() : result),
      clear: id => serial(() => clear(id)),
      navigated: (id, change) => change.url && !site(change.url) ? serial(() => clear(id)) : Promise.resolve(),
      closedApproval: windowId => serial(async () => {
        const records = await read();
        let changed = false;
        for (const [id, record] of Object.entries(records)) {
          if (record.pending?.windowId === windowId) { delete records[id]; changed = true; }
        }
        if (changed) await write(records);
        const pending = await readCloudSessions();
        let sessionChanged = false;
        for (const [reqId, item] of Object.entries(pending)) {
          if (item.windowId === windowId) { delete pending[reqId]; sessionChanged = true; }
        }
        if (sessionChanged) await writeCloudSessions(pending);
      }),
    };
  }
  if (typeof module !== 'undefined') module.exports = { createCloudConnection, defaultUploadCloudSession, ORIGIN, RELAY_CAPABILITIES };
  else {
    const bridge = createCloudConnection(chrome, { readForm: readCloudPoshmarkForm, runCommand: runCloudMarketplaceCommand,
      verifyApproval: CloudApprovals.createApprovalVerifier(chrome).verify });
    chrome.runtime.onMessage.addListener((message, sender, reply) => {
      if (message?.type !== 'cloud.connection') return;
      bridge.handle(message, sender).then(reply).catch(() => reply({ ok: false }));
      return true;
    });
    chrome.tabs.onRemoved.addListener(id => { void bridge.clear(id).catch(() => {}); });
    chrome.tabs.onUpdated.addListener((id, change) => { void bridge.navigated(id, change).catch(() => {}); });
    chrome.windows.onRemoved?.addListener(id => { void bridge.closedApproval(id).catch(() => {}); });
  }
})();
