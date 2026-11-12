/* The hosted page's data contracts: form reads, and the seller's relayed marketplace commands.
   No credentials or arbitrary URLs. Commands are only the ones the hosted API relays. */
(function () {
  const strings = new Set(['title', 'description', 'brand', 'condition', 'primaryColor', 'secondaryColor', 'size', 'sku', 'zipCode', 'category_path', 'department', 'package_dimensions_in']);
  const numbers = new Set(['price', 'cost', 'quantity', 'weight_oz']);
  const fieldKeys = new Set(['key', 'path', 'label', 'value', 'required', 'multi', 'selection_only', 'options', 'input_type',
    'inventory_groups', 'min_value', 'max_value', 'step', 'locked', 'option_values', 'clears', 'help', 'max_values', 'max_length',
    'options_source', 'on_form', 'option_groups', 'option_aliases', 'reveal', 'slot_labels', 'accept', 'section', 'width',
    'control_width', 'cascade', 'option_amounts', 'group', 'group_help', 'section_help', 'placeholder', 'prefix', 'suffix',
    'label_hidden', 'counter', 'presentation', 'option_help', 'category_specific']);
  function listing(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (strings.has(key)) {
        const max = key === 'description' ? 20000 : key === 'category_path' ? 1000 : 200;
        if (typeof item !== 'string' || item.length > max) return null;
      } else if (numbers.has(key)) {
        if (item !== null && (typeof item !== 'number' || !Number.isFinite(item) || item < 0 || item > 2147483647
          || (key === 'quantity' && (!Number.isInteger(item) || item < 1)))) return null;
      } else return null;
      result[key] = item;
    }
    return result;
  }
  function form(value) {
    if (value?.marketplace !== 'poshmark' || value.native !== true || !Array.isArray(value.fields) || value.fields.length > 100) return null;
    const fields = [];
    for (const field of value.fields) {
      if (!field || typeof field.key !== 'string' || field.key.length > 160 || typeof field.label !== 'string'
          || !/^(price|cost|direct_category_ids\.poshmark|marketplace_photos\.poshmark|poshmark_specifics\.[a-zA-Z0-9_.]+)$/.test(field.key)
          || /token|secret|cookie|password|authorization/i.test(field.key) || !Array.isArray(field.options)) return null;
      fields.push(Object.fromEntries(Object.entries(field).filter(([key]) => fieldKeys.has(key))));
    }
    const result = { marketplace: 'poshmark', native: true, known: value.known === true, page_labels: value.page_labels === true,
      category_id: String(value.category_id || ''), fields };
    try { return JSON.stringify(result).length <= 6_000_000 ? result : null; } catch { return null; }
  }
  // Marketplace commands the hosted API relays, approved and confirmed there and signed (background/cloud-approvals.js).
  const COMMANDS = new Set(['marketplace.execute', 'marketplace.result', 'marketplace.delete', 'job.cancel']);
  // The marketplaces the web reaches only through the seller's Chrome.
  const MARKETPLACES = new Set(['poshmark', 'mercari', 'depop']);
  // Named commands other than marketplace actions, each with exactly the inputs it takes:
  // a maximum string length, the set of values allowed, or the pattern a value must match.
  const READS = {
    'marketplace.sales_history': { marketplace: new Set(['depop', 'poshmark', 'mercari']), settled: 'order_ids' },
    'marketplace.sessions': {},
    'marketplace.connect': { marketplace: new Set(['depop', 'poshmark', 'mercari']) },
    // Inventory import: a page of the seller's own active listings, or one of them.
    'marketplace.import.page': { marketplace: MARKETPLACES, cursor: 300 },
    'marketplace.import.listing': { marketplace: MARKETPLACES, listing_id: /^[a-z0-9][a-z0-9-]{0,200}$/ },
  };
  const PROMOTION_COMMANDS = new Set(['marketplace.promotion_check', 'marketplace.promote', 'marketplace.promotion_result', 'marketplace.like_events']);
  function promotion(value) {
    const p = value.payload;
    const id = /^[A-Za-z0-9-]{1,64}$/;
    if (typeof value.job_id !== 'string' || !id.test(value.job_id) || p.job_id !== value.job_id || size(value) > 10000) return false;
    let keys = ['job_id', 'request_id', 'approval'];
    if (value.type === 'marketplace.promotion_result') {
      keys.push('action_id');
      if (!/^[A-Za-z0-9-]{6,64}$/.test(p.action_id || '')) return false;
    } else if (value.type === 'marketplace.like_events') {
      keys.push('marketplace');
      if (!['poshmark', 'depop'].includes(p.marketplace)) return false;
    } else {
      keys.push('marketplace', 'action', 'listing_id');
      const ids = { poshmark: /^[a-f0-9]{24}$/, mercari: /^m\d{6,}$/, depop: /^[a-z0-9][a-z0-9-]{0,200}$/ };
      if (!Object.hasOwn(ids, p.marketplace) || typeof p.listing_id !== 'string' || !ids[p.marketplace].test(p.listing_id)
          || !['share', 'offer'].includes(p.action) || (p.action === 'share' && p.marketplace !== 'poshmark')) return false;
      if (value.type === 'marketplace.promotion_check') {
        if (p.action === 'offer') {
          keys.push('discount_percent');
          if (!Number.isInteger(p.discount_percent) || p.discount_percent < 1 || p.discount_percent > 99) return false;
        }
      } else {
        keys.push('action_id', 'confirm', 'price_cents', 'offer_price_cents', 'floor_cents', 'shipping_discount_id', 'gap_ms');
        const cents = n => Number.isInteger(n) && n > 0 && n <= 2147483647;
        if (p.confirm !== true || !/^[A-Za-z0-9-]{6,64}$/.test(p.action_id || '') || !cents(p.price_cents)) return false;
        if (p.action === 'offer' ? (!cents(p.offer_price_cents) || !cents(p.floor_cents) || p.offer_price_cents < p.floor_cents)
            : (p.offer_price_cents !== null || p.floor_cents !== null)) return false;
        if (p.shipping_discount_id !== null && (typeof p.shipping_discount_id !== 'string' || !/^[A-Za-z0-9-]{1,40}$/.test(p.shipping_discount_id))) return false;
        if (Object.hasOwn(p, 'gap_ms') && (p.action !== 'share' || !Number.isInteger(p.gap_ms) || p.gap_ms < 4000 || p.gap_ms > 30000)) return false;
      }
    }
    return Object.keys(p).every(key => keys.includes(key));
  }
  const MIMES = new Set(['image/jpeg', 'image/png', 'image/webp']);
  function plain(value) { return Boolean(value) && typeof value === 'object' && !Array.isArray(value); }
  function size(value) { try { return JSON.stringify(value).length; } catch { return Infinity; } }
  function command(value) {
    if (!plain(value) || Object.keys(value).some(key => !['type', 'job_id', 'message_id', 'payload'].includes(key))) return null;
    const payload = value.payload;
    if (!/^[0-9a-f]{32}$/.test(value.message_id || '') || !plain(payload) || payload.request_id !== value.message_id) return null;
    // The hosted API's signed approval of the command, checked by the worker before it runs.
    if (Object.hasOwn(payload, 'approval') && (typeof payload.approval !== 'string' || payload.approval.length > 11000)) return null;
    if (value.type === 'job.vendoo_api') {
      if (payload.listing_mode !== 'vendoo' || !/^[a-f0-9-]{1,64}$/i.test(value.job_id || '')
          || size(value) > 2_000_000 || !Array.isArray(payload.ops) || !payload.ops.length) return null;
      const allowed = new Set(['session', 'new_item_id', 'subscription', 'upload_photo', 'create_item', 'get_item', 'update_item', 'resolve_labels']);
      if (payload.ops.some(op => !plain(op) || !allowed.has(op.op))) return null;
      for (const op of payload.ops) {
        if (op.op === 'upload_photo' && (!plain(op.photo) || !/^[a-f0-9-]{1,64}$/i.test(op.photo.id || '') || !MIMES.has(op.photo.mime_type)
            || Object.keys(op.photo).some(key => !['id', 'mime_type', 'extension'].includes(key)))) return null;
        if (['get_item', 'update_item'].includes(op.op) && !/^[A-Za-z0-9]{20}$/.test(op.item_id || '')) return null;
        if (op.op === 'update_item' && (!op.expected_update_time || !plain(op.updates)
            || Object.keys(op.updates).some(path => !/^(generalDetails\.|listings\.[a-z]+\.(overrides|categorySpecifics|marketplaceSpecifics)\.|labels$)/.test(path)))) return null;
      }
      return value;
    }
    if (PROMOTION_COMMANDS.has(value.type)) return promotion(value) ? value : null;
    if (Object.hasOwn(READS, value.type)) {
      if (value.type === 'marketplace.sales_history' && (!Object.hasOwn(payload, 'marketplace') || !Object.hasOwn(payload, 'settled'))) return null;
      const inputs = READS[value.type];
      for (const [key, item] of Object.entries(payload)) {
        if (key === 'request_id' || key === 'approval' || (key === 'job_id' && item === null)) continue;
        if (!Object.hasOwn(inputs, key)) return null;
        const rule = inputs[key];
        if (rule === 'order_ids') {
          if (!Array.isArray(item) || item.length > 2000 || !item.every(id => typeof id === 'string' && /^[A-Za-z0-9-]{1,64}$/.test(id))) return null;
          continue;
        }
        if (typeof item !== 'string') return null;
        if (rule instanceof Set ? !rule.has(item) : rule instanceof RegExp ? !rule.test(item) : item.length > rule) return null;
      }
      return value.job_id === null ? value : null;
    }
    if (!COMMANDS.has(value.type) || !/^[A-Za-z0-9-]{1,64}$/.test(String(value.job_id || ''))
        || size(value) > 2_000_000) return null;
    if (value.type === 'job.cancel') return /^[A-Za-z0-9-]{1,64}$/.test(String(payload.job_id || '')) ? value : null;
    // Video is not part of the web app; a relayed List never carries one.
    if (!MARKETPLACES.has(payload.marketplace) || payload.video) return null;
    return value;
  }
  function photos(value) {
    if (!Array.isArray(value) || value.length > 30) return null;
    let total = 0;
    for (const photo of value) {
      if (!plain(photo) || Object.keys(photo).some(key => !['id', 'mime_type', 'base64'].includes(key))
          || !/^[a-f0-9-]{1,64}$/i.test(photo.id || '') || !MIMES.has(photo.mime_type)
          || typeof photo.base64 !== 'string' || !/^[A-Za-z0-9+/]*={0,2}$/.test(photo.base64)) return null;
      total += photo.base64.length;
      if (photo.base64.length > 28_000_000 || total > 80_000_000) return null;
    }
    return value;
  }
  // The Bridge's own reply, forwarded to the hosted API, which records it.
  function reply(value) { return plain(value) && size(value) <= 1_000_000 ? value : null; }
  const protocol = { listing, form, command, photos, reply };
  if (typeof module !== 'undefined') module.exports = protocol;
  else globalThis.CloudMarketplaceProtocol = protocol;
})();
