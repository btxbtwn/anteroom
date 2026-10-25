// Native actions are separate worker commands in native-marketplaces.js.
// A legacy fill message must never advance into publishing.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'PING') sendResponse({ ok: true, platform: 'poshmark' });
  if (msg.type === 'START_FILL' || msg.type === 'START_POSHMARK') {
    sendResponse({ ok: false, error: 'Use Save draft in Anteroom. Publishing requires a separate confirmation.' });
  }
});
