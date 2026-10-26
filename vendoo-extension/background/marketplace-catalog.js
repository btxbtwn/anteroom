// What a marketplace's own catalog calls a listing's size, brand and tags.
// Catalogs are read-only, read from the seller's Chrome just before a direct
// listing writes anything, and kept for a few hours. A listing whose ids
// cannot all be found stops with missing_fields before the first write.
const MARKETPLACE_CATALOG_TTL_MS = 6 * 60 * 60 * 1000;
const marketplaceCatalogs = new Map();

// One cached catalog read. Failures are not kept.
async function marketplaceCatalog(key, read) {
  const hit = marketplaceCatalogs.get(key);
  if (hit && Date.now() - hit.at < MARKETPLACE_CATALOG_TTL_MS) return hit.value;
  const value = await read();
  marketplaceCatalogs.set(key, { at: Date.now(), value });
  return value;
}

// A catalog read writes nothing, so every failure is safe to retry. A public
// catalog that answers 401/403 is blocking the browser, not signing it out.
function marketplaceCatalogFailure(marketplace, what, error, { signedIn = false } = {}) {
  const label = marketplaceLabel(marketplace);
  let code = error?.code || 'network';
  if (code === 'uncertain') code = 'network';
  if (code === 'signed_out' && !signedIn) code = 'challenge';
  const message = {
    signed_out: error?.message,
    rate_limited: error?.message,
    challenge: `${label} blocked Anteroom from reading its ${what}. Open ${label} in Chrome, finish any check, then try again.`,
    schema_drift: `${label}'s ${what} changed. Anteroom needs an update before listing there.`,
  }[code] || `Anteroom could not read ${label}'s ${what}, so nothing was listed. Try again.`;
  return marketplaceError(message, { code, rejected: true, status: error?.status ?? null,
    marketplace_message: error?.marketplace_message || '', retry_after_s: error?.retry_after_s ?? null });
}

async function marketplaceCatalogRead(marketplace, what, read, options) {
  try {
    return await read();
  } catch (error) {
    if (error?.code === 'missing_fields') throw error;
    throw marketplaceCatalogFailure(marketplace, what, error, options);
  }
}

// Names compared the way a seller reads them: case, accents and punctuation aside.
function marketplaceNameKey(value) {
  return String(value ?? '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '');
}

function sizeKey(value) {
  return String(value ?? '').trim().toUpperCase().replace(/\s+/g, ' ');
}

// 2XL and XXL are the same size; catalogs spell it one way or the other.
function sizeSpellings(key) {
  const numbered = /^(\d)X([LS])$/.exec(key);
  if (numbered && Number(numbered[1]) > 1) return [key, `${'X'.repeat(Number(numbered[1]))}${numbered[2]}`];
  const lettered = /^(X{2,})([LS])$/.exec(key);
  if (lettered) return [key, `${lettered[1].length}X${lettered[2]}`];
  return [key];
}

const SIZE_WORDS = [[/^(?:xxx|3x)[- ]?large$/i, 'XXXL'], [/^(?:xx|2x)[- ]?large$/i, 'XXL'], [/^(?:x|extra)[- ]?large$/i, 'XL'],
  [/^large$/i, 'L'], [/^medium$/i, 'M'], [/^small$/i, 'S'], [/^(?:x|extra)[- ]?small$/i, 'XS'], [/^(?:xx|2x)[- ]?small$/i, 'XXS'],
  [/^one[- ]size(?: fits (?:all|most))?$/i, 'ONE SIZE']];

// The ways a seller's size can be read, most literal first:
// "XL (18/20)" -> XL (18/20), XL, 18/20, 18, 20; "32x30" -> 32; "US 10" -> 10.
function sizeCandidates(raw) {
  const out = [];
  const add = value => {
    for (const key of sizeSpellings(sizeKey(value))) if (key && !out.includes(key)) out.push(key);
  };
  const text = String(raw ?? '').trim();
  if (!text) return out;
  add(text);
  const bare = text.replace(/^(?:(?:us|size|women'?s|men'?s|boys'?|girls'?|kids'?|toddler)\s+)+/i, '');
  add(bare);
  const base = bare.replace(/\s*\([^)]*\)\s*$/, '');
  add(base);
  for (const [pattern, size] of SIZE_WORDS) if (pattern.test(base)) add(size);
  const waist = /^w?(\d{2})\s*(?:x|\/)\s*l?\d{2}$/i.exec(base) || /^w(\d{2})\s*l\d{2}$/i.exec(base);
  if (waist) add(waist[1]);
  const inner = /\(([^)]*)\)/.exec(bare)?.[1];
  if (inner) {
    add(inner);
    for (const part of inner.split(/[/,-]/)) add(part);
  }
  return out;
}

// How a catalog size is written: exactly, then without "Waist"/"Neck" words,
// a set marker such as "(Girl)", or a petite set's trailing P.
function sizeEntryKeys(labels, { petite = false } = {}) {
  const tight = new Set();
  const loose = new Set();
  for (const label of labels) {
    const key = sizeKey(label);
    if (!key) continue;
    tight.add(key);
    let plain = key.replace(/^(?:WAIST|NECK|CHEST|INSEAM|LENGTH|US)\s+/, '').replace(/\s*\([^)]*\)\s*$/, '').replace(/\s+IN\.?$/, '');
    if (petite && /^[0-9X]*[0-9SML]P$/.test(plain)) plain = plain.slice(0, -1);
    loose.add(plain);
  }
  return { tight, loose };
}

// The first seller spelling the catalog knows. Entries are tried in the
// caller's order of preference; returns every entry the winning spelling
// matches so the caller can refuse an ambiguous one.
function matchSize(raw, entries) {
  for (const candidate of sizeCandidates(raw)) {
    for (const tier of ['tight', 'loose']) {
      const found = entries.filter(entry => entry.keys[tier].has(candidate));
      if (found.length) return found;
    }
  }
  return [];
}

// boys / girls from the listing's department or breadcrumbs, never guessed.
function listingKidsGender(listing) {
  const sources = [listing?.department, listing?.ebay_specifics?.department, listing?.category_path,
    ...Object.values(listing?.marketplace_categories || {})];
  for (const source of sources) {
    const text = String(source || '').toLowerCase();
    const boys = /\bboy'?s?'?\b/.test(text);
    const girls = /\bgirl'?s?'?\b/.test(text);
    if (boys !== girls) return boys ? 'boys' : 'girls';
  }
  return null;
}

// The listing's fit, as Poshmark names its size sets.
function listingSizeFit(listing) {
  const sources = [listing?.sizeType, listing?.ebay_specifics?.sizeType, listing?.poshmark_specifics?.sizeType,
    listing?.mercari_specifics?.sizeType, listing?.depop_specifics?.sizeType, listing?.etsy_specifics?.sizeType,
    listing?.vinted_specifics?.sizeType, listing?.sellwild_specifics?.sizeType];
  const texts = sources.map(value => String(value || '').trim().toLowerCase()).filter(Boolean);
  const text = texts.find(value => !/^(?:regular|standard|normal)$/.test(value)) || texts[0] || '';
  if (!text || /^(?:regular|standard|normal)$/.test(text)) return null;
  if (/petite/.test(text)) return 'petite';
  if (/plus/.test(text)) return 'plus';
  if (/big/.test(text)) return 'big_&_tall';
  if (/tall/.test(text)) return 'tall';
  if (/maternity/.test(text)) return 'maternity';
  if (/junior/.test(text)) return 'juniors';
  return text;
}

function listingTags(listing) {
  const raw = Array.isArray(listing?.tags) ? listing.tags : String(listing?.tags || '').split(',');
  return [...new Set(raw.map(tag => String(tag || '').trim()).filter(Boolean))];
}
