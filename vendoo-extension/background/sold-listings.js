// Read eBay's sold-items search for Studio's sourcing prices. eBay answers only a
// real browser, so each search loads in one background tab that is closed when
// the batch ends. Studio turns the cards into prices; nothing here buys or lists.

const EBAY_SOLD_LOAD_TIMEOUT_MS = 20000;
const EBAY_SOLD_PAUSE_MS = 1500;

function ebaySoldSearchUrl(query) {
  const params = new URLSearchParams({
    _nkw: query,
    LH_Sold: '1',
    LH_Complete: '1',
    LH_ItemCondition: '3000', // pre-owned
    LH_PrefLoc: '1', // US only, so prices are in USD
    _ipg: '60',
    rt: 'nc',
  });
  return `https://www.ebay.com/sch/i.html?${params}`;
}

// Runs in the eBay page.
function readEbaySoldCards() {
  const text = (el, selector) => el.querySelector(selector)?.innerText.trim() || '';
  const list = document.querySelector('ul.srp-results');
  const cards = [];
  for (const li of list ? list.children : []) {
    // Everything after this notice matches only some of the words searched.
    if (/REWRITE_START/.test(li.className)) break;
    if (!li.dataset.listingid) continue;
    cards.push({
      id: li.dataset.listingid,
      title: text(li, '.s-card__title'),
      caption: text(li, '.s-card__caption'),
      price: text(li, '.s-card__price'),
      // The price shown is the asking price; the accepted offer is hidden.
      offer: /best offer accepted/i.test(li.innerText),
    });
  }
  return { loaded: !!list, cards };
}

function waitForTabLoad(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(onUpdated);
      reject(new Error('eBay took too long to load'));
    }, timeoutMs);

    function onUpdated(id, info, tab) {
      // The blank page the tab opens on finishes loading too.
      if (id !== tabId || info.status !== 'complete' || !/^https:\/\/www\.ebay\.com\//.test(tab.url || '')) return;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve();
    }

    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

// Every search that loaded, keyed by its query. A page that is not search results
// (a sign-in or robot check) ends the batch; Studio keeps what was read before it.
async function readEbaySoldListings(queries) {
  const results = {};
  let tab = null;
  try {
    for (const query of queries) {
      const url = ebaySoldSearchUrl(query);
      if (tab) await new Promise((resolve) => setTimeout(resolve, EBAY_SOLD_PAUSE_MS));
      else tab = await chrome.tabs.create({ url: 'about:blank', active: false });
      const loaded = waitForTabLoad(tab.id, EBAY_SOLD_LOAD_TIMEOUT_MS);
      await chrome.tabs.update(tab.id, { url });
      await loaded;
      const frames = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readEbaySoldCards });
      const page = frames[0]?.result;
      if (!page?.loaded) throw new Error('eBay did not show search results');
      results[query] = page.cards;
    }
    return { ok: true, results };
  } catch (err) {
    return { ok: false, results, error: err.message };
  } finally {
    if (tab) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}
