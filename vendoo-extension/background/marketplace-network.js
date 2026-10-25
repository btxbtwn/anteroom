let marketplaceNetworkReady = null;

function ensureMarketplaceRequestContext() {
  if (!marketplaceNetworkReady) {
    // Scope the web client's request context to this extension's Depop and
    // Mercari API calls. Ordinary browsing and requests from other extensions are untouched.
    marketplaceNetworkReady = chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [17001, 17002],
      addRules: [{ id: 17002, priority: 1,
        action: { type: 'modifyHeaders', requestHeaders: [
          { header: 'Origin', operation: 'set', value: 'https://www.mercari.com' },
          { header: 'Referer', operation: 'set', value: 'https://www.mercari.com/sell/' },
        ] },
        condition: { regexFilter: '^https://www\\.mercari\\.com/v1/api$',
          initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods: ['post'] },
      }, { id: 17001, priority: 1,
        action: { type: 'modifyHeaders', requestHeaders: [
          { header: 'Origin', operation: 'set', value: 'https://www.depop.com' },
          { header: 'Referer', operation: 'set', value: 'https://www.depop.com/sellinghub/drafts/' },
        ] },
        condition: { regexFilter: '^https://webapi\\.depop\\.com/(presentation/api/v1/(drafts|pictures)/|api/v3/drafts/products/)',
          initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods: ['get', 'post', 'put'] },
      }],
    });
  }
  return marketplaceNetworkReady;
}
