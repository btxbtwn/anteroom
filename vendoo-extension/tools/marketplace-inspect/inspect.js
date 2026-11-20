// Marketplace page inspection, for developers investigating a marketplace's protocol.
// Paste into the DevTools console on a signed-in marketplace page (Poshmark, Mercari,
// Depop or Etsy) and press Enter. Read-only: it never saves, posts or changes the page.
//
// It prints and downloads <marketplace>-inspection.json: the page URL without its query,
// title, visible field and button labels, listing links, and public script URLs. Form
// values, inline state, cookies and headers are left out. To download one of the listed
// public scripts instead, set SCRIPT_URL to it before running.
//
// This runs only in your own DevTools. It is not part of Anteroom Bridge: the store
// package and the Mac app's copy leave tools/ out.
const SCRIPT_URL = null;

async function inspectMarketplaceDocument(origin, requestedScript) {
  if (location.href === 'about:blank') return { waiting: true };
  if (location.origin !== origin) return { error: 'The marketplace redirected to another origin.' };
  const scripts = [...new Set([...document.scripts].map(script => script.src).filter(Boolean)
    .concat(performance.getEntriesByType('resource').filter(entry => entry.initiatorType === 'script').map(entry => entry.name)))];
  const publicScript = raw => {
    const url = new URL(raw);
    return url.protocol === 'https:' && /\.m?js$/.test(url.pathname) && (url.origin === origin
      || ['.depop.com', '.mercari.com', '.mercdn.net', '.etsystatic.com'].some(suffix => url.hostname.endsWith(suffix))
      || (origin === 'https://poshmark.com' && url.hostname === 'd2gjrq7hs8he14.cloudfront.net'));
  };
  const sanitized = raw => { const url = new URL(raw); return `${url.origin}${url.pathname}`; };
  if (requestedScript) {
    const selected = scripts.find(src => publicScript(src) && sanitized(src) === requestedScript);
    if (!selected) return { error: 'Choose a public script observed on this marketplace page.' };
    const response = await fetch(requestedScript, { credentials: 'omit', signal: AbortSignal.timeout(25000) });
    if (!response.ok || !/javascript/.test(response.headers.get('content-type') || '')) return { error: 'The public script was unavailable.' };
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 2_000_000) return { error: 'The public script exceeds the inspection limit.' };
        chunks.push(value);
      }
    } finally { await reader.cancel().catch(() => {}); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return { ok: true, script_url: requestedScript, source: new TextDecoder().decode(bytes) };
  }
  const visible = el => Boolean(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
  let authenticated = null;
  let apiStatus = null;
  let draftSchema = null;
  if (origin === 'https://www.depop.com') {
    const cookie = String(document.cookie || '').split(';').map(value => value.trim()).find(value => value.startsWith('access_token='));
    if (!cookie) authenticated = false;
    else try {
      const response = await fetch('https://webapi.depop.com/presentation/api/v1/drafts/?limit=1', {
        headers: { Authorization: `Bearer ${decodeURIComponent(cookie.slice(13))}`, Accept: 'application/json' },
        credentials: 'omit', signal: AbortSignal.timeout(8000),
      });
      apiStatus = response.status;
      if (response.ok) {
        const body = await response.json();
        authenticated = true;
        draftSchema = { response_keys: Object.keys(body), item_keys: Object.keys(body.objects?.[0] || {}) };
      } else if ([401, 403].includes(response.status)) authenticated = false;
    } catch { /* Preserve an unknown result rather than export session data. */ }
  }
  if (origin === 'https://www.etsy.com') {
    const shopId = performance.getEntriesByType('resource').map(entry => entry.name.match(/\/api\/v3\/ajax\/(?:bespoke\/)?shop\/(\d+)\//)?.[1]).find(Boolean);
    if (shopId) try {
      const response = await fetch(`/api/v3/ajax/bespoke/shop/${shopId}/listing-editor-data/create/0`, {
        credentials: 'include', headers: { Accept: 'application/json', 'X-Transform-Response': 'camel-case' }, signal: AbortSignal.timeout(10000),
      });
      apiStatus = response.status;
      if (response.ok) {
        const body = await response.json();
        const listing = body.listing || {};
        authenticated = true;
        draftSchema = { response_keys: Object.keys(body), listing_keys: Object.keys(listing), field_keys: Object.keys(listing.formFields || {}),
          core_details_data: body.coreDetailsData,
          empty_form_defaults: Object.fromEntries(['coreDetails', 'whoMade', 'whenMade', 'isSupply', 'quantity', 'price', 'listingType', 'variations']
            .map(key => [key, listing.formFields?.[key] ?? null])) };
        const search = await fetch(`/api/v3/ajax/shop/${shopId}/listings/v3/search?state=draft&limit=1&offset=0`, {
          credentials: 'include', headers: { Accept: 'application/json', 'X-Transform-Response': 'camel-case' }, signal: AbortSignal.timeout(10000),
        });
        if (search.ok) {
          const rows = await search.json();
          draftSchema.search_schema = Object.fromEntries(Object.entries(rows).map(([key, value]) => [key,
            Array.isArray(value) ? { array: true, item_keys: Object.keys(value[0] || {}) } : typeof value]));
          if (Array.isArray(rows)) draftSchema.search_item_keys = Object.keys(rows[0] || {});
        } else draftSchema.search_status = search.status;
      } else if ([401, 403].includes(response.status)) authenticated = false;
    } catch { /* Keep unavailable editor evidence unknown. */ }
  }
  const bodyText = document.body?.innerText || '';
  return {
    ok: true, url: sanitized(location.href), title: document.title.slice(0, 200), ready: document.readyState, authenticated,
    api_status: apiStatus, draft_schema: draftSchema,
    api_paths: [...new Set(performance.getEntriesByType('resource').map(entry => entry.name).filter(raw => {
      const url = new URL(raw); return url.origin === origin && /^\/api\/v3\/ajax\/(?:bespoke\/)?shop\/\d+\//.test(url.pathname);
    }).map(sanitized))].slice(0, 60),
    api_parameters: performance.getEntriesByType('resource').map(entry => new URL(entry.name))
      .filter(url => url.origin === origin && url.pathname.endsWith('/listings/v3/search'))
      .slice(-1).map(url => ({ path: url.pathname, names: [...url.searchParams.keys()] })),
    links: [...document.querySelectorAll('a[href]')].filter(visible).map(el => el.href)
      .filter(href => { const url = new URL(href); return url.origin === origin && /\/your\/shops\/|sellinghub|\/listings|\/sell(?:\/|$)/.test(url.pathname); })
      .slice(0, 60).map(sanitized),
    page_signals: { sign_in: /sign in to your account|sign in to continue/i.test(bodyText),
      not_found: /page (was |could )?not found|page you.re looking for/i.test(bodyText),
      challenge: /verify you are human|press and hold|security check|captcha/i.test(bodyText) },
    errors: [...document.querySelectorAll('[role="alert"],h1,h2,p')].filter(visible)
      .map(el => String(el.innerText || '').trim()).filter(value => /something went wrong|couldn.t load|unable to load|try refreshing|error occurred/i.test(value))
      .slice(0, 5).map(value => value.slice(0, 250)),
    fields: [...document.querySelectorAll('input,textarea,select')].filter(el => (visible(el) || el.type === 'file') && el.type !== 'password')
      .slice(0, 100).map(el => ({ tag: el.tagName, id: el.id, name: el.name, type: el.type,
        label: el.getAttribute('aria-label') || el.placeholder || '', ...(el.type === 'file' ? { accept: el.accept } : {}) })),
    controls: [...document.querySelectorAll('button,[role="button"]')].filter(visible).slice(0, 100)
      .map(el => ({ label: String(el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 100), disabled: Boolean(el.disabled) })),
    scripts: scripts.filter(publicScript).map(sanitized).slice(-100),
  };
}

inspectMarketplaceDocument(location.origin, SCRIPT_URL).then(result => {
  console.log(result);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
  link.download = `${location.hostname.replace(/^www\./, '').split('.')[0]}-inspection.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
});
