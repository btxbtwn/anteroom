(async function () {
  const requestId = location.hash.slice(1);
  const approve = document.getElementById('approve');
  const deny = document.getElementById('deny');
  const message = document.getElementById('message');
  const send = action => chrome.runtime.sendMessage({ type: 'cloud.connection', action, requestId });
  try {
    const details = await send('cloud_session_details');
    if (!details?.ok) throw new Error();
    document.getElementById('account').textContent = details.email;
    approve.disabled = false;
    deny.disabled = false;
  } catch {
    message.textContent = 'This request expired. Return to Anteroom Settings and try again.';
  }
  approve.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true;
    deny.disabled = true;
    message.textContent = 'Connecting Poshmark…';
    try {
      const result = await send('cloud_session_approve');
      if (!result?.ok) {
        message.textContent = result?.error || 'Could not connect Poshmark. Make sure you are signed in to Poshmark in Chrome and try again.';
        return;
      }
      window.close();
    } catch {
      message.textContent = 'Could not connect Poshmark. Return to Anteroom Settings and try again.';
    }
  });
  deny.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true;
    deny.disabled = true;
    try { await send('cloud_session_deny'); } finally { window.close(); }
  });
})();
