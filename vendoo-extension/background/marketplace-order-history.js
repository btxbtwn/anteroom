// Read the seller's own order history on each Chrome marketplace, with the
// amounts the marketplace reports. Anteroom records sales only from these
// orders, never from a listing that merely looks sold.
//
// Each order comes back in one shape, with no buyer details:
//   { order_id, status, final, sold_at, currency,
//     totals: { item_revenue, shipping_credit, refunds, fees, shipping_cost },
//     lines: [{ line_id, listing_id, alt_ids, weight, item_revenue }] }
// Amounts are decimal strings in dollars. ``totals`` is the whole order;
// Anteroom splits it across the lines by ``weight`` unless a line carries its
// own ``item_revenue``. ``final`` means the order will not change again, so a
// later read can skip its detail. ``status`` is one of paid,
// partially_refunded, refunded or cancelled.
//
// Recorded against the seller's live accounts on 2026-11-02:
//   Depop     receipts list + one receipt per order (webapi presentation API).
//   Poshmark  vm-rest sales list + each order page's initial state.
//   Mercari   userItemsQuery (sold_out, trading) + OrderStatusConsolidated.

const HISTORY_MAX_PAGES = 40;
// Order details read per pass. The rest are read on the next pass.
const HISTORY_MAX_DETAILS = 400;
const HISTORY_STATUSES = new Set(['paid', 'partially_refunded', 'refunded', 'cancelled']);
const MERCARI_USER_ITEMS = '88c1f24f1ee3617c5e4b04f33d4f9aec7357e55cb94fea8affc78571df6368f3';
const MERCARI_ORDER_STATUS = '1e2de463bdf2fb33c7c328ad2659d4ef20ace0e4ff0c526b64d6502b26c23f6a';

async function historyJson(marketplace, url, options = {}) {
  try {
    const { data } = await marketplaceFetch(url, { ...options, marketplace, step: 'sales_history',
      headers: { Accept: 'application/json', ...options.headers }, deadline: Date.now() + 60000 });
    return data;
  } catch (error) {
    if (error?.status === 404) return null;
    throw error;
  }
}

// "8.23", 8.23, "28.0" → "8.23". Anything else → null.
function historyMoney(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const clean = typeof value === 'string' ? value.replace(/[$,\s]/g, '') : String(value);
  if (!/^-?\d+(\.\d+)?$/.test(clean)) return null;
  const number = Number(clean);
  return Number.isFinite(number) ? number.toFixed(2) : null;
}

function historyCents(value) {
  return Number.isInteger(value) ? (value / 100).toFixed(2) : null;
}

function historySum(...values) {
  if (values.some(value => value === null)) return null;
  return values.reduce((total, value) => total + Number(value), 0).toFixed(2);
}

function historyTime(value) {
  const time = value ? new Date(value) : null;
  return time && Number.isFinite(time.getTime()) ? time.toISOString() : null;
}

function historyOrder(order) {
  if (!order.order_id || !HISTORY_STATUSES.has(order.status) || !order.lines.length) return null;
  return { currency: 'USD', ...order };
}

// Skip settled details while walking every page: an older page can still
// contain an order left unread by a previous pass's detail limit.
function historyPending(ids, settled) {
  return ids.filter(id => !settled.has(id));
}

// Order lists come newest first, so most passes stop at the first page whose
// orders were all settled before. A full walk, at least every six hours and
// after a pass that left orders unread, still reaches older open orders.
const HISTORY_FULL_WALK_MS = 6 * 60 * 60 * 1000;
const HISTORY_FULL_WALKS_KEY = 'orderHistoryFullWalks';

async function historyFullWalks() {
  try { return (await chrome.storage.local.get(HISTORY_FULL_WALKS_KEY))[HISTORY_FULL_WALKS_KEY] || {}; } catch { return {}; }
}

async function historyFullWalkDue(marketplace) {
  const at = Number((await historyFullWalks())[marketplace]);
  return !(Date.now() - at < HISTORY_FULL_WALK_MS);
}

async function recordHistoryWalk(marketplace, full, more) {
  if (!full && !more) return;
  const walks = await historyFullWalks();
  // A pass that left orders unread is followed by a full walk.
  if (more) delete walks[marketplace];
  else walks[marketplace] = Date.now();
  try { await chrome.storage.local.set({ [HISTORY_FULL_WALKS_KEY]: walks }); } catch { /* The next pass walks everything. */ }
}

function historySettledPage(ids, settled) {
  return ids.length > 0 && ids.every(id => settled.has(id));
}

// ── Depop ────────────────────────────────────────────────────────────────

const DEPOP_FINAL = new Set(['delivered', 'delivered_pending_feedback', 'refunded', 'cancelled']);

function depopHistoryOrder(receipt) {
  const lines = (Array.isArray(receipt?.lines) ? receipt.lines : [])
    .filter(line => SALE_LISTING_ID.depop.test(String(line?.slug ?? '')))
    .map(line => ({ line_id: String(line.id), listing_id: String(line.slug), alt_ids: [String(line.product_id ?? '')].filter(Boolean),
      weight: historyMoney(line.product_amount), item_revenue: historyMoney(line.product_amount) }));
  if (lines.length !== receipt?.lines?.length) return null;
  const refund = historyMoney(receipt?.refund_details?.refund_amount) ?? '0.00';
  const fees = receipt?.fee_breakdown || {};
  let status = 'paid';
  if (receipt?.status === 'cancelled') status = 'cancelled';
  else if (receipt?.status === 'refunded') status = Number(receipt?.payout?.amount) > 0 ? 'partially_refunded' : 'refunded';
  return historyOrder({
    order_id: String(receipt?.purchase_id ?? ''),
    status,
    final: DEPOP_FINAL.has(receipt?.status),
    sold_at: historyTime(receipt?.sold_timestamp),
    totals: {
      item_revenue: historyMoney(receipt?.items_amount),
      // Buyer shipping pays Depop's label; none of it reaches the seller.
      shipping_credit: '0.00',
      refunds: refund,
      fees: historySum(historyMoney(receipt?.payment_fee), historyMoney(fees.seller_fee?.amount) ?? '0.00',
        historyMoney(fees.boosting_fee?.amount) ?? '0.00'),
      shipping_cost: historyMoney(receipt?.shipping_details?.seller_shipping_amount) ?? '0.00',
    },
    lines,
  });
}

async function depopOrderHistory(settled, incremental = false) {
  await ensureMarketplaceRequestContext();
  const auth = await depopBearer();
  const read = path => historyJson('depop', `https://webapi.depop.com/presentation/api/v1/receipts/${path}`,
    { credentials: 'omit', headers: { Authorization: auth } });
  const orders = [];
  let after = null;
  let details = 0;
  let more = false;
  let unreadable = 0;
  for (let page = 0; page < HISTORY_MAX_PAGES; page += 1) {
    if (page) await salePause(true);
    const body = await read(`?role=seller&limit=50${after ? `&after=${encodeURIComponent(after)}` : ''}`);
    if (!Array.isArray(body?.objects)) throw new Error('Depop receipts missing');
    const ids = body.objects.map(row => String(row?.purchase_id ?? '')).filter(id => /^\d{1,20}$/.test(id));
    const pending = historyPending(ids, settled);
    for (const id of pending) {
      if (details >= HISTORY_MAX_DETAILS) { more = true; break; }
      await salePause(false);
      details += 1;
      const order = depopHistoryOrder(await read(`${id}/`));
      if (order) orders.push(order);
      else unreadable += 1;
    }
    after = body.page_info?.last || null;
    if (!more && incremental && historySettledPage(ids, settled)) break;
    if (page === HISTORY_MAX_PAGES - 1 && body.page_info?.has_more) more = true;
    if (more || !body.page_info?.has_more || !after) break;
  }
  return { orders, more, unreadable };
}

// ── Poshmark ─────────────────────────────────────────────────────────────

function poshmarkHistoryOrder(order, displayStatus) {
  const value = amount => historyMoney(amount?.val);
  const cancelled = order?.state === 'cancelled' || Boolean(order?.cancelled_on);
  const lines = (Array.isArray(order?.line_items) ? order.line_items : [])
    .filter(line => SALE_LISTING_ID.poshmark.test(String(line?.product_id ?? '')))
    .map(line => ({ line_id: String(line.id), listing_id: String(line.product_id), alt_ids: [],
      weight: value(line.price_amount), item_revenue: null }));
  if (lines.length !== order?.line_items?.length) return null;
  // A cancelled order still shows earnings on Poshmark; none were paid.
  const zero = '0.00';
  return historyOrder({
    order_id: String(order?.id ?? ''),
    status: cancelled ? 'cancelled' : 'paid',
    final: cancelled || displayStatus === 'Order Complete',
    sold_at: historyTime(order?.inventory_booked_at),
    totals: cancelled
      ? { item_revenue: zero, shipping_credit: zero, refunds: zero, fees: zero, shipping_cost: zero }
      : {
        item_revenue: value(order?.total_price_amount),
        // The buyer pays Poshmark's shipping label; the seller pays only a shipping discount they offered.
        shipping_credit: zero,
        refunds: zero,
        fees: historySum(value(order?.pm_fee_amount), value(order?.commission_amount) ?? zero),
        shipping_cost: value(order?.seller_shipping_discount_amount) ?? zero,
      },
    lines,
  });
}

async function poshmarkOrderHistory(settled, incremental = false) {
  const { user_id: seller } = await poshmarkAccount();
  const orders = [];
  const seen = new Set();
  let maxId = null;
  let details = 0;
  let more = false;
  let unreadable = 0;
  for (let page = 0; page < HISTORY_MAX_PAGES; page += 1) {
    if (page) await salePause(true);
    const request = { filters: {}, count: 100, ...(maxId ? { max_id: maxId } : {}) };
    const body = await saleJson(`https://poshmark.com/vm-rest/users/${seller}/orders/sales?request=${encodeURIComponent(JSON.stringify(request))}`,
      { credentials: 'include' });
    if (body?.error || !Array.isArray(body?.data?.sales_summary)) throw new Error('Poshmark sales missing');
    const rows = body.data.sales_summary.filter(row => SALE_LISTING_ID.poshmark.test(String(row?.id ?? '')) && !seen.has(String(row.id)));
    rows.forEach(row => seen.add(String(row.id)));
    const ids = rows.map(row => String(row.id));
    const pending = historyPending(ids, settled);
    for (const row of rows.filter(row => pending.includes(String(row.id)))) {
      if (details >= HISTORY_MAX_DETAILS) { more = true; break; }
      await salePause(false);
      details += 1;
      const response = await saleRead(`https://poshmark.com/order/sales/${row.id}`, { credentials: 'include' });
      const html = response ? await response.text() : '';
      let state = null;
      try { state = JSON.parse(/__INITIAL_STATE__\s*=\s*({[^<]*});/.exec(html)?.[1] || 'null'); } catch { state = null; }
      const detail = state?.$_order_details?.order;
      if (!detail || String(detail.id) !== String(row.id)) throw new Error('Poshmark order page changed');
      const order = poshmarkHistoryOrder(detail, row.display_status);
      if (order) orders.push(order);
      else unreadable += 1;
    }
    maxId = body.meta?.next_max_id || null;
    if (!more && incremental && historySettledPage(ids, settled)) break;
    if (page === HISTORY_MAX_PAGES - 1 && maxId) more = true;
    if (more || !maxId) break;
  }
  return { orders, more, unreadable };
}

// ── Mercari ──────────────────────────────────────────────────────────────

// "Sold on 10/11/26" → noon UTC that day. Mercari shows no time.
function mercariSoldDay(text) {
  const match = /(\d{1,2})\/(\d{1,2})\/(\d{2})\b/.exec(String(text || ''));
  if (!match) return null;
  const [, month, day, year] = match;
  return historyTime(`20${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}T12:00:00Z`);
}

// The seller's side of the order summary: everything above "Buyer also paid".
function mercariSellerSummary(items) {
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {
    if (item?.style === 'buyer_paid') break;
    if (item?.formattedPrice) rows.push(item);
  }
  const sold = rows.find(row => /^sold price$/i.test(String(row.title).trim()));
  const made = rows.find(row => row.style === 'total');
  if (!sold || !made) return null;
  let fees = 0;
  let shipping = 0;
  for (const row of rows) {
    if (row === sold || row === made) continue;
    const parsed = historyMoney(row.formattedPrice);
    if (parsed === null) return null;
    const amount = Number(parsed);
    if (!Number.isFinite(amount) || amount > 0) return null;
    if (/ship|label|postage/i.test(String(row.title))) shipping -= amount;
    else fees -= amount;
  }
  const soldPrice = historyMoney(sold.formattedPrice);
  const madePrice = historyMoney(made.formattedPrice);
  if (soldPrice === null || madePrice === null) return null;
  const revenue = Number(soldPrice);
  // The seller's lines must add up to what Mercari says they made.
  if (Math.abs(revenue - fees - shipping - Number(madePrice)) > 0.005) return null;
  return { item_revenue: revenue.toFixed(2), shipping_credit: '0.00', refunds: '0.00', fees: fees.toFixed(2), shipping_cost: shipping.toFixed(2) };
}

function mercariHistoryOrder(item, contents) {
  const components = Array.isArray(contents?.componentContents) ? contents.componentContents : [];
  const summary = components.map(part => part?.summaryContent).find(Boolean);
  const bundle = components.map(part => part?.orderContent).find(Boolean);
  const cancelled = /cancel/i.test(String(contents?.orderState || ''));
  const members = item.itemType === 'bundle' ? (bundle?.items || []) : [{ id: item.id, price: item.price }];
  const lines = members.filter(member => SALE_LISTING_ID.mercari.test(String(member?.id ?? '')))
    .map(member => ({ line_id: String(member.id), listing_id: String(member.id), alt_ids: [], weight: historyCents(member.price), item_revenue: null }));
  if (lines.length !== members.length) return null;
  const totals = cancelled
    ? { item_revenue: '0.00', shipping_credit: '0.00', refunds: '0.00', fees: '0.00', shipping_cost: '0.00' }
    : mercariSellerSummary(summary?.orderItems);
  if (!totals) return null;
  return historyOrder({
    order_id: String(contents?.orderId || item.activeOrder?.orderId || ''),
    status: cancelled ? 'cancelled' : 'paid',
    final: cancelled || contents?.orderState === 'order_complete',
    sold_at: mercariSoldDay(summary?.soldTimeText),
    totals,
    lines,
  });
}

async function mercariOrderHistory(settled, incremental = false) {
  const deadline = Date.now() + 15 * 60 * 1000;
  const session = await mercariCurrentSession(deadline, true);
  const seller = Number(session?.user?.userId);
  if (!Number.isInteger(seller)) throw new Error('Seller not found');
  // Both are reads; the label ending in "Query" tells mercariRequest so.
  const query = (operationName, sha256Hash, variables) => mercariRequest(
    { operationName, variables, extensions: { persistedQuery: { version: 1, sha256Hash } } },
    { deadline, operationName: operationName.endsWith('Query') ? operationName : `${operationName}Query` });
  const orders = [];
  let unreadable = 0;
  let details = 0;
  let more = false;
  for (const status of ['trading', 'sold_out']) {
    for (let page = 1; page <= HISTORY_MAX_PAGES && !more; page += 1) {
      await salePause(page > 1);
      const data = await query('userItemsQuery', MERCARI_USER_ITEMS, { userItemsInput: { sellerId: seller, status, keyword: '',
        sortBy: 'updated', sortType: 'desc', page, includeTotalCount: true } });
      const items = data?.userItems?.items;
      if (!Array.isArray(items)) throw new Error('Mercari sold items missing');
      // A bundle's items are sold through the bundle's own order.
      const sold = items.filter(item => item?.activeOrder?.orderId && /^[mb]\d{6,}$/.test(String(item.id)));
      const pending = sold.filter(item => !settled.has(String(item.activeOrder.orderId)));
      for (const item of pending) {
        if (details >= HISTORY_MAX_DETAILS) { more = true; break; }
        await salePause(false);
        details += 1;
        const detail = await query('OrderStatusConsolidated', MERCARI_ORDER_STATUS,
          { input: { referenceId: item.id, referenceType: item.itemType === 'bundle' ? 'bundle' : 'item' } });
        const order = mercariHistoryOrder(item, detail?.orderStatus?.contents);
        if (order) orders.push(order);
        else unreadable += 1;
      }
      if (!more && incremental && historySettledPage(sold.map(item => String(item.activeOrder.orderId)), settled)) break;
      if (page === HISTORY_MAX_PAGES && data.userItems.pagination?.hasNext) more = true;
      if (!data.userItems.pagination?.hasNext) break;
    }
  }
  return { orders, more, unreadable };
}

const ORDER_HISTORY_READERS = { depop: depopOrderHistory, poshmark: poshmarkOrderHistory, mercari: mercariOrderHistory };

async function readMarketplaceOrderHistory(marketplace, settledIds) {
  if (nativeOperationsRunning()) return { ok: true, orders: [], skipped: true };
  const read = ORDER_HISTORY_READERS[marketplace];
  if (!read) return { ok: false, error: 'Could not read sales.' };
  const settled = new Set((Array.isArray(settledIds) ? settledIds : []).filter(id => typeof id === 'string' && id.length <= 64));
  try {
    const full = await historyFullWalkDue(marketplace);
    const { orders, more = false, unreadable = 0 } = await read(settled, !full);
    await recordHistoryWalk(marketplace, full, more);
    return { ok: true, orders, more, unreadable };
  } catch (error) {
    const code = ['signed_out', 'challenge', 'rate_limited', 'network', 'schema_drift', 'rejected'].includes(error?.code)
      ? error.code : 'schema_drift';
    marketplaceLog(marketplace, 'sales_history', { error_code: code, status: error?.status });
    return { ok: false, error: code };
  }
}

async function handleMarketplaceOrderHistory(msg) {
  try {
    const payload = msg.payload || {};
    sendBrowserResult(msg, await readMarketplaceOrderHistory(payload.marketplace, payload.settled));
  } catch {
    sendBrowserResult(msg, { ok: false, error: 'Could not read sales.' });
  }
}
