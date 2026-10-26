let marketplaceNetworkReady = null;

function marketplaceHeaderRule(id, headers, regexFilter, requestMethods) {
  return { id, priority: 1,
    action: { type: 'modifyHeaders', requestHeaders: Object.entries(headers).map(([header, value]) => ({ header, operation: 'set', value })) },
    condition: { regexFilter, initiatorDomains: [chrome.runtime.id], resourceTypes: ['xmlhttprequest'], requestMethods } };
}

// Scope each web client's request context to this extension's own Depop,
// Mercari, Poshmark, Sellwild and Vinted API calls. Ordinary browsing and requests
// from other extensions are untouched. A failed setup is tried again next time.
// One short pattern per rule. Chrome rejects a regexFilter whose compiled
// form is over 2KB, and one alternation covering every Depop path did that,
// which stopped every direct Depop call before it was sent.
const DEPOP_PAGE_HEADERS = { Origin: 'https://www.depop.com', Referer: 'https://www.depop.com/sellinghub/drafts/' };
const DEPOP_PAGE_METHODS = ['get', 'post', 'put', 'delete'];
const DEPOP_PAGE_RULES = [
  [17001, '^https://webapi\\.depop\\.com/presentation/api/v1/attributes/'],
  [17011, '^https://webapi\\.depop\\.com/presentation/api/v1/drafts/'],
  [17012, '^https://webapi\\.depop\\.com/presentation/api/v1/pictures/'],
  [17013, '^https://webapi\\.depop\\.com/presentation/api/v1/products/'],
  [17014, '^https://webapi\\.depop\\.com/presentation/api/v1/addresses/'],
  [17015, '^https://webapi\\.depop\\.com/presentation/api/v1/users/'],
  [17016, '^https://webapi\\.depop\\.com/presentation/api/v1/shipping/'],
  [17017, '^https://webapi\\.depop\\.com/presentation/api/v1/shops/\\d+/products/by-status/(sold|selling|onsale)/'],
  [17018, '^https://webapi\\.depop\\.com/api/v1/addresses/'],
  [17019, '^https://webapi\\.depop\\.com/api/v1/shipping-providers/'],
  [17020, '^https://webapi\\.depop\\.com/api/v3/drafts/products/'],
  [17022, '^https://webapi\\.depop\\.com/api/v1/shop/\\d+/products/'],
];

function ensureMarketplaceRequestContext() {
  if (!marketplaceNetworkReady) {
    marketplaceNetworkReady = chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [17001, 17002, 17003, 17004, 17005, 17006, 17011, 17012, 17013, 17014, 17015, 17016, 17017, 17018, 17019, 17020, 17021, 17022, 17023, 17024],
      addRules: [
        ...DEPOP_PAGE_RULES.map(([id, regexFilter]) => marketplaceHeaderRule(id, DEPOP_PAGE_HEADERS, regexFilter, DEPOP_PAGE_METHODS)),
        marketplaceHeaderRule(17002, { Origin: 'https://www.mercari.com', Referer: 'https://www.mercari.com/sell/' },
          '^https://www\\.mercari\\.com/v1/(api|initialize)$', ['get', 'post']),
        marketplaceHeaderRule(17003, { Origin: 'https://sellwild.com', Referer: 'https://sellwild.com/sell' },
          '^https://sellwild\\.com/api/(auth/session|addresses|listings/create|listings/\\d+|listings/\\d+/confirm-photos|seller/listings/bulk-status|search/listings)(\\?|$)', ['get', 'post']),
        marketplaceHeaderRule(17004, { Origin: 'https://www.depop.com', Referer: 'https://www.depop.com/sellinghub/' },
          '^https://webapi\\.depop\\.com/api/v1/receipts/', ['get']),
        marketplaceHeaderRule(17005, { Origin: 'https://poshmark.com', Referer: 'https://poshmark.com/create-listing' },
          '^https://poshmark\\.com/(vm-rest/|api/|create-listing$)', ['get', 'post', 'put', 'delete']),
        marketplaceHeaderRule(17024, { Origin: 'https://www.vinted.com', Referer: 'https://www.vinted.com/' },
          '^https://www\\.vinted\\.com/(items/new|api/v2/users/current|api/v2/items/\\d+(/delete)?)(\\?|$)', ['get', 'post']),
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
