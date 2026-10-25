let marketplaceNetworkReady = null;

function marketplaceHeaderRule(id, headers, regexFilter, requestMethods) {
  return { id, priority: 1,
    action: { type: 'modifyHeaders', requestHeaders: Object.entries(headers).map(([header, value]) => ({ header, operation: 'set', value })) },
    condition: { regexFilter, initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods } };
}

// Scope each web client's request context to this extension's own Depop,
// Mercari, Poshmark and Sellwild API calls. Ordinary browsing and requests
// from other extensions are untouched. A failed setup is tried again next time.
function ensureMarketplaceRequestContext() {
  if (!marketplaceNetworkReady) {
    marketplaceNetworkReady = chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [17001, 17002, 17003, 17004, 17005, 17006],
      addRules: [
        marketplaceHeaderRule(17001, { Origin: 'https://www.depop.com', Referer: 'https://www.depop.com/sellinghub/drafts/' },
          '^https://webapi\\.depop\\.com/(presentation/api/v1/(drafts|pictures|products)/|api/v3/drafts/products/)', ['get', 'post', 'put', 'delete']),
        marketplaceHeaderRule(17002, { Origin: 'https://www.mercari.com', Referer: 'https://www.mercari.com/sell/' },
          '^https://www\\.mercari\\.com/v1/(api|initialize)$', ['get', 'post']),
        marketplaceHeaderRule(17003, { Origin: 'https://sellwild.com', Referer: 'https://sellwild.com/sell' },
          '^https://sellwild\\.com/api/(auth/session|addresses|listings/create|listings/\\d+|listings/\\d+/confirm-photos|seller/listings/bulk-status)$', ['get', 'post']),
        marketplaceHeaderRule(17004, { Origin: 'https://www.depop.com', Referer: 'https://www.depop.com/sellinghub/' },
          '^https://webapi\\.depop\\.com/api/v1/receipts/', ['get']),
        marketplaceHeaderRule(17005, { Origin: 'https://poshmark.com', Referer: 'https://poshmark.com/create-listing' },
          '^https://poshmark\\.com/(vm-rest/|api/|create-listing$)', ['get', 'post', 'put', 'delete']),
        // Depop's accelerated photo bucket expects its web app as the referrer.
        marketplaceHeaderRule(17006, { Referer: 'https://web.depop.com/' },
          '^https://[a-z0-9.-]+\\.s3-accelerate\\.amazonaws\\.com/', ['put']),
      ],
    }).catch(error => {
      marketplaceNetworkReady = null;
      console.warn('[MARKET] header rules failed', String(error?.message || error));
      throw marketplaceError('Chrome could not prepare the marketplace connection. Try again.', { code: 'network' });
    });
  }
  return marketplaceNetworkReady;
}
