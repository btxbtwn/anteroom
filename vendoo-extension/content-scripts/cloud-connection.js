/* Narrow page-to-extension transport. Never forward arbitrary extension commands. */
(function () {
  const ORIGIN = 'https://myanteroom.com';
  if (window !== window.top || location.origin !== ORIGIN) return;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const ACTIONS = ['status', 'connect', 'disconnect', 'poshmark_form', 'marketplace_save_draft'];
  // Requests that use the connection rather than change it.
  const USES = ['poshmark_form', 'marketplace_save_draft'];
  const SUPPORTED = ['poshmark.form.read', 'marketplace.save_draft'];
  let accountId = null;
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window || event.origin !== ORIGIN || !data || data.source !== 'anteroom-cloud' || data.version !== 1
        || !UUID.test(data.requestId || '') || !UUID.test(data.accountId || '')
        || !ACTIONS.includes(data.action)) return;
    const keys = ['source', 'version', 'requestId', 'action', 'accountId', ...(data.action === 'connect' ? ['email'] : []),
      ...(data.action === 'poshmark_form' ? ['listing'] : []), ...(data.action === 'marketplace_save_draft' ? ['command'] : [])];
    if (Object.keys(data).some(key => !keys.includes(key))) return;
    if (data.action === 'connect' && (typeof data.email !== 'string' || data.email.length > 254)) return;
    const listing = data.action === 'poshmark_form' ? CloudMarketplaceProtocol.listing(data.listing) : null;
    if (data.action === 'poshmark_form' && !listing) return;
    // Relayed as is: only the extension worker can verify the server's signature.
    const command = data.action === 'marketplace_save_draft' ? CloudMarketplaceProtocol.command(data.command) : null;
    if (data.action === 'marketplace_save_draft' && !command) return;
    if (data.action === 'disconnect') {
      if (accountId === data.accountId) accountId = null;
    } else if (!USES.includes(data.action)) accountId = data.accountId;
    const message = { type: 'cloud.connection', action: data.action, accountId: data.accountId,
      ...(data.action === 'connect' ? { email: data.email } : {}), ...(listing ? { listing } : {}), ...(command ? { command } : {}) };
    if (USES.includes(data.action) && accountId !== data.accountId) {
      respond({ ok: false, error_code: 'account_changed' }); return;
    }
    function respond(result) {
      if (data.action === 'marketplace_save_draft') {
        window.postMessage({ source: 'anteroom-extension', version: 1, requestId: data.requestId,
          ...CloudMarketplaceProtocol.draftResult(result) }, ORIGIN);
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
        extensionVersion: typeof result?.extensionVersion === 'string' ? result.extensionVersion.slice(0, 30) : '', supportedActions: Array.isArray(result?.supportedActions) ? result.supportedActions.filter(action => SUPPORTED.includes(action)) : [] }, ORIGIN);
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
