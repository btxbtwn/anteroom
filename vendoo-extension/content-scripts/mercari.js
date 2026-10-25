// Native actions are separate worker commands in native-marketplaces.js.
// Legacy fill messages cannot save or publish a native listing.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'PING') sendResponse({ ok: true, platform: 'mercari' });
  if (msg.type === 'START_FILL' || msg.type === 'START_MERCARI') {
    sendResponse({ ok: false, error: 'Use Save draft in Anteroom. Publishing requires a separate confirmation.' });
  }
});
