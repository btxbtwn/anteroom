/* Narrow page-to-extension transport. Never forward arbitrary extension commands. */
(function () {
  const ORIGIN = 'https://peaceful-meadowstone-6944.fly.dev';
  if (window !== window.top || location.origin !== ORIGIN) return;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let accountId = null;
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window || event.origin !== ORIGIN || !data || data.source !== 'anteroom-cloud' || data.version !== 1
        || !UUID.test(data.requestId || '') || !UUID.test(data.accountId || '')
        || !['status', 'connect', 'disconnect'].includes(data.action)) return;
    const keys = ['source', 'version', 'requestId', 'action', 'accountId', ...(data.action === 'connect' ? ['email'] : [])];
    if (Object.keys(data).some(key => !keys.includes(key))) return;
    if (data.action === 'connect' && (typeof data.email !== 'string' || data.email.length > 254)) return;
    if (data.action === 'disconnect') {
      if (accountId === data.accountId) accountId = null;
    } else accountId = data.accountId;
    const message = { type: 'cloud.connection', action: data.action, accountId: data.accountId,
      ...(data.action === 'connect' ? { email: data.email } : {}) };
    function respond(result) {
      window.postMessage({ source: 'anteroom-extension', version: 1, requestId: data.requestId,
        ok: result?.ok === true, state: ['connected', 'disconnected', 'pending'].includes(result?.state) ? result.state : 'unavailable',
        extensionVersion: typeof result?.extensionVersion === 'string' ? result.extensionVersion.slice(0, 30) : '', supportedActions: [] }, ORIGIN);
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
