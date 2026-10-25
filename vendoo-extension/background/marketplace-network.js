let marketplaceNetworkReady = null;

function ensureMarketplaceRequestContext() {
  if (!marketplaceNetworkReady) {
    // Scope the web client's request context to this extension's Depop,
    // Mercari and Sellwild API calls. Ordinary browsing and requests from other extensions are untouched.
    marketplaceNetworkReady = chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [17001, 17002, 17003],
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
        condition: { regexFilter: '^https://webapi\\.depop\\.com/(presentation/api/v1/(drafts|pictures|products)/|api/v3/drafts/products/)',
          initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods: ['get', 'post', 'put', 'delete'] },
      }, { id: 17003, priority: 1,
        action: { type: 'modifyHeaders', requestHeaders: [
          { header: 'Origin', operation: 'set', value: 'https://sellwild.com' },
          { header: 'Referer', operation: 'set', value: 'https://sellwild.com/sell' },
        ] },
        condition: { regexFilter: '^https://sellwild\\.com/api/(auth/session|addresses|listings/create|listings/\\d+/confirm-photos|seller/listings/bulk-status)$',
          initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods: ['get', 'post'] },
      }],
    });
  }
  return marketplaceNetworkReady;
}
