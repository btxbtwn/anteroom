(async function () {
  const requestId = location.hash.slice(1);
  const approve = document.getElementById('approve');
  const deny = document.getElementById('deny');
  const message = document.getElementById('message');
  const send = action => chrome.runtime.sendMessage({ type: 'cloud.connection', action, requestId });
  let marketplaceName = '';
  try {
    const details = await send('cloud_session_details');
    const names = { poshmark: 'Poshmark', mercari: 'Mercari', depop: 'Depop' };
    if (!details?.ok || !Object.hasOwn(names, details.marketplace)) throw new Error();
    marketplaceName = names[details.marketplace];
    document.getElementById('title').textContent = `Connect ${marketplaceName} in the cloud?`;
    document.getElementById('prompt').textContent = `Connect the ${marketplaceName} account signed in to this Chrome profile.`;
    document.getElementById('account').textContent = details.email;
    document.getElementById('scope').textContent = details.marketplace === 'depop'
      ? 'Anteroom encrypts your Depop cookies for sales checks, imports, and standalone delists. Keep Chrome connected for listing actions and offers. You can disconnect in Settings.'
      : 'Anteroom encrypts your marketplace cookies so listing actions, sales checks, and enabled automations work while Chrome is closed. You can disconnect in Settings.';
    approve.disabled = false;
    deny.disabled = false;
  } catch {
    message.textContent = 'This request expired. Return to Anteroom Settings and try again.';
  }
  approve.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true;
    deny.disabled = true;
    message.textContent = `Connecting ${marketplaceName}…`;
    try {
      const result = await send('cloud_session_approve');
      if (!result?.ok) {
        message.textContent = result?.error || `Sign in to ${marketplaceName} in Chrome and try again.`;
        return;
      }
      window.close();
    } catch {
      message.textContent = `Could not connect ${marketplaceName}. Return to Anteroom Settings and try again.`;
    }
  });
  deny.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true;
    deny.disabled = true;
    try { await send('cloud_session_deny'); } finally { window.close(); }
  });
})();
