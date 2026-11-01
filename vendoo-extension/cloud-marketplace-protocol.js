/* The hosted page's data contracts: form reads, and the seller's relayed marketplace commands.
   No credentials or arbitrary URLs. Commands are only the four the hosted API relays. */
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
  // Marketplace commands the hosted API relays, already approved and confirmed there.
  const COMMANDS = new Set(['marketplace.execute', 'marketplace.result', 'marketplace.delete', 'job.cancel']);
  const MARKETPLACES = new Set(['poshmark', 'mercari', 'depop', 'sellwild', 'vinted']);
  const MIMES = new Set(['image/jpeg', 'image/png', 'image/webp']);
  function plain(value) { return Boolean(value) && typeof value === 'object' && !Array.isArray(value); }
  function size(value) { try { return JSON.stringify(value).length; } catch { return Infinity; } }
  function command(value) {
    if (!plain(value) || Object.keys(value).some(key => !['type', 'job_id', 'message_id', 'payload'].includes(key))) return null;
    const payload = value.payload;
    if (!COMMANDS.has(value.type) || !/^[0-9a-f]{32}$/.test(value.message_id || '') || !plain(payload)
        || payload.request_id !== value.message_id || !/^[A-Za-z0-9-]{1,64}$/.test(String(value.job_id || ''))
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
