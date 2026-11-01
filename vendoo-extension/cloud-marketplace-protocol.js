/* The hosted reader's data contract; no credentials, URLs or mutation commands. */
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
  // A web Save draft: the server's signed command, relayed unchanged. The worker
  // verifies the signature; the page and content script only check its shape.
  function command(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).length !== 2 || typeof value.body !== 'string' || typeof value.signature !== 'string') return null;
    if (value.body.length > 2_000_000 || !/^[A-Za-z0-9_-]+$/.test(value.body) || !/^[A-Za-z0-9_-]{86}$/.test(value.signature)) return null;
    return { body: value.body, signature: value.signature };
  }
  const DRAFT_CODES = new Set(['not_connected', 'account_changed', 'invalid_command', 'busy', 'unavailable', 'signed_out', 'challenge',
    'rate_limited', 'missing_fields', 'rejected', 'uncertain', 'network', 'schema_drift', 'not_found']);
  // What the page learns: whether the draft was saved and whether Anteroom has the
  // result. The listing record on the server carries the rest.
  function draftResult(value) {
    const ok = value?.ok === true;
    return { ok, recorded: value?.recorded === true,
      ...(ok ? {} : { error_code: DRAFT_CODES.has(value?.error_code) ? value.error_code : 'unavailable' }) };
  }
  const protocol = { listing, form, command, draftResult };
  if (typeof module !== 'undefined') module.exports = protocol;
  else globalThis.CloudMarketplaceProtocol = protocol;
})();
