/* Narrow page-to-extension transport. Only connection, form reads and the relayed marketplace commands. */
(function () {
  const ORIGIN = 'https://myanteroom.com';
  if (window !== window.top || location.origin !== ORIGIN) return;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let accountId = null;
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window || event.origin !== ORIGIN || !data || data.source !== 'anteroom-cloud' || data.version !== 1
        || !UUID.test(data.requestId || '') || !UUID.test(data.accountId || '')
        || !['status', 'connect', 'disconnect', 'poshmark_form', 'marketplace_command'].includes(data.action)) return;
    const relay = data.action === 'marketplace_command';
    const keys = ['source', 'version', 'requestId', 'action', 'accountId', ...(data.action === 'connect' ? ['email'] : []),
      ...(data.action === 'poshmark_form' ? ['listing'] : []), ...(relay ? ['command', 'photos'] : [])];
    if (Object.keys(data).some(key => !keys.includes(key))) return;
    if (data.action === 'connect' && (typeof data.email !== 'string' || data.email.length > 254)) return;
    const listing = data.action === 'poshmark_form' ? CloudMarketplaceProtocol.listing(data.listing) : null;
    if (data.action === 'poshmark_form' && !listing) return;
    const command = relay ? CloudMarketplaceProtocol.command(data.command) : null;
    const photos = relay ? CloudMarketplaceProtocol.photos(data.photos) : null;
    if (relay && (!command || !photos)) return;
    if (data.action === 'disconnect') {
      if (accountId === data.accountId) accountId = null;
    } else if (data.action !== 'poshmark_form' && !relay) accountId = data.accountId;
    const message = { type: 'cloud.connection', action: data.action, accountId: data.accountId,
      ...(data.action === 'connect' ? { email: data.email } : {}), ...(listing ? { listing } : {}), ...(relay ? { command, photos } : {}) };
    if ((data.action === 'poshmark_form' || relay) && accountId !== data.accountId) {
      respond({ ok: false, error_code: 'account_changed' }); return;
    }
    function respond(result) {
      if (relay) {
        // The Bridge's reply goes to the hosted API, which records what the marketplace did.
        const reply = result?.ok ? CloudMarketplaceProtocol.reply(result.reply) : null;
        const code = ['not_connected', 'invalid_input', 'account_changed'].includes(result?.error_code) ? result.error_code : 'unavailable';
        window.postMessage({ source: 'anteroom-extension', version: 1, requestId: data.requestId,
          ok: Boolean(reply), ...(reply ? { reply } : { error_code: code }) }, ORIGIN);
        return;
      }
      if (data.action === 'poshmark_form') {
        const changed = accountId !== data.accountId;
        const form = !changed && result?.ok ? CloudMarketplaceProtocol.form(result.form) : null;
        const code = changed ? 'account_changed' : ['not_connected', 'invalid_input', 'account_changed', 'busy', 'signed_out', 'challenge', 'rate_limited', 'schema_drift', 'network'].includes(result?.error_code) ? result.error_code : 'unavailable';
        window.postMessage({ source: 'anteroom-extension', version: 1, requestId: data.requestId,
          ok: Boolean(form), ...(form ? { form } : { error_code: code }) }, ORIGIN);
        return;
      }
      window.postMessage({ source: 'anteroom-extension', version: 1, requestId: data.requestId,
        ok: result?.ok === true, state: ['connected', 'disconnected', 'pending'].includes(result?.state) ? result.state : 'unavailable',
        extensionVersion: typeof result?.extensionVersion === 'string' ? result.extensionVersion.slice(0, 30) : '', supportedActions: Array.isArray(result?.supportedActions) ? result.supportedActions.filter(action => ['poshmark.form.read', 'marketplace.actions', 'vinted.choices', 'sellwild.account'].includes(action)) : [] }, ORIGIN);
    }
    try { chrome.runtime.sendMessage(message).then(respond).catch(() => respond(null)); }
    catch { respond(null); }
  });
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id === chrome.runtime.id && message?.type === 'cloud.connection.probe') {
      respond({ current: accountId !== null && message.accountId === accountId });
    }
  });
})();
