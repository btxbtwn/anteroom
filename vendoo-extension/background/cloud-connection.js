/* Browser-local site permission, form reads, and the seller's relayed marketplace commands. No cloud credentials. */
(function () {
  const ORIGIN = 'https://myanteroom.com';
  const KEY = 'anteroom_cloud_connections';
  const APPROVALS_KEY = 'anteroom_cloud_approvals';
  const TTL = 2 * 60 * 1000;
  // A remembered approval lasts this long; then the seller approves the account again.
  const APPROVAL_TTL = 30 * 24 * 60 * 60 * 1000;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const PAGE_ACTIONS = new Set(['status', 'connect', 'disconnect']);
  const APPROVAL_ACTIONS = new Set(['details', 'approve', 'deny']);
  // What a relaying Bridge runs: marketplace actions, and each named read under its own name,
  // and how it checks them: a signed approval on every relayed command, binding the photos
  // relayed with it (background/cloud-approvals.js). It no longer declares the writes-only
  // `marketplace.signed_approvals` of Bridges 0.2.56 to 0.2.67, which the server drops later.
  const RELAY_CAPABILITIES = ['marketplace.actions', 'marketplace.sessions', 'marketplace.sales_history', 'marketplace.promotions', 'marketplace.import', 'vendoo.drafts', 'marketplace.signed_commands'];

  function createCloudConnection(api, { now = Date.now, randomId = () => crypto.randomUUID(), readForm = null, runCommand = null,
    verifyApproval = null } = {}) {
    // A Bridge that runs relayed commands must check the hosted API's signature on each one.
    if (runCommand && !verifyApproval) throw new Error('Relayed commands need an approval check.');
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
    const page = sender => sender.id === api.runtime.id && Number.isInteger(sender.tab?.id)
      && sender.frameId === 0 && typeof sender.documentId === 'string'
      && site(sender.url) && site(sender.tab.url) && (!sender.origin || sender.origin === ORIGIN);
    const status = state => ({ ok: true, state, extensionVersion: api.runtime.getManifest().version, supportedActions: [...(readForm ? ['poshmark.form.read'] : []), ...(runCommand ? RELAY_CAPABILITIES : [])] });
    async function read() { return (await api.storage.session.get(KEY))[KEY] || {}; }
    async function write(records) { await api.storage.session.set({ [KEY]: records }); }
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
    async function clear(tabId) {
      const records = await read();
      const record = records[tabId];
      delete records[tabId];
      await write(records);
      await closeApproval(record);
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
    }
    async function handle(message, sender) {
      if (!message || typeof message !== 'object' || message.type !== 'cloud.connection') return undefined;
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
          ? relayCommand(message, sender) : serial(() => handle(message, sender)),
      clear: id => serial(() => clear(id)),
      navigated: (id, change) => change.url && !site(change.url) ? serial(() => clear(id)) : Promise.resolve(),
      closedApproval: windowId => serial(async () => {
        const records = await read();
        let changed = false;
        for (const [id, record] of Object.entries(records)) {
          if (record.pending?.windowId === windowId) { delete records[id]; changed = true; }
        }
        if (changed) await write(records);
      }),
    };
  }
  if (typeof module !== 'undefined') module.exports = { createCloudConnection, ORIGIN, RELAY_CAPABILITIES };
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
