(async function () {
  const requestId = location.hash.slice(1);
  const approve = document.getElementById('approve');
  const deny = document.getElementById('deny');
  const message = document.getElementById('message');
  const send = action => chrome.runtime.sendMessage({ type: 'cloud.connection', action, requestId });
  try {
    const details = await send('details');
    if (!details?.ok) throw new Error();
    document.getElementById('account').textContent = details.email;
    approve.disabled = false; deny.disabled = false;
  } catch { message.textContent = 'This request expired. Return to Anteroom and connect again.'; }
  approve.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true; deny.disabled = true;
    try {
      const result = await send('approve');
      if (!result?.ok) throw new Error();
      window.close();
    } catch { message.textContent = 'The page or account changed. Return to Anteroom and connect again.'; }
  });
  deny.addEventListener('click', async event => {
    if (!event.isTrusted) return;
    approve.disabled = true; deny.disabled = true;
    try { await send('deny'); } finally { window.close(); }
  });
})();
