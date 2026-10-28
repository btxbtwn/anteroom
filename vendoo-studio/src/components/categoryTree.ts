/** A marketplace category the seller can save, named by its full path. */
export interface CategoryChoice {
  label: string;
  id: string;
}

const SEPARATOR = /\s*(?:>|›)\s*/;

/** Split a stored path on either separator the catalogs use. */
export function categorySegments(label: string): string[] {
  return label.split(SEPARATOR).map((part) => part.trim()).filter(Boolean);
}

/** A stored path as the picker shows it: "Men › Bottoms › Jeans". */
export function categoryPathLabel(label: string): string {
  return categorySegments(label).join(" › ");
}

function startsWord(text: string, word: string): boolean {
  for (let at = text.indexOf(word); at !== -1; at = text.indexOf(word, at + 1)) {
    if (at === 0 || !/[a-z0-9]/.test(text[at - 1])) return true;
  }
  return false;
}

/**
 * Choices whose path starts a word with every word typed, in the order given,
 * at most `limit`. "men jeans" finds "Men › Bottoms › Jeans" but not "Women › …".
 */
export function matchCategoryChoices(choices: readonly CategoryChoice[], query: string, limit = 100): CategoryChoice[] {
  const words = query.toLowerCase().split(/[\s›>]+/).filter(Boolean);
  const matches: CategoryChoice[] = [];
  for (const choice of choices) {
    if (matches.length >= limit) break;
    const path = choice.label.toLowerCase();
    if (words.every((word) => startsWord(path, word))) matches.push(choice);
  }
  return matches;
}

/** Path-shaped category options for a direct listing form. eBay loads its own tree. */
export function pathCategoryChoices(field: {
  key: string;
  label: string;
  options?: readonly string[];
  optionValues?: Record<string, string>;
}): CategoryChoice[] | null {
  if (field.key === "direct_category_ids.ebay") return null;
  const named = field.label === "Category"
    || field.key.endsWith("nativeCategoryId")
    || field.key.startsWith("direct_category_ids.");
  const options = field.options ?? [];
  if (!named || !options.some((option) => /[>›]/.test(option))) return null;
  return options.map((label) => ({ label, id: field.optionValues?.[label] ?? label }));
}

export interface CatalogCategoryNode {
  id?: string;
  parent_id?: string;
  label: string;
  path: string;
  is_leaf?: boolean;
  has_children?: boolean;
}

/** Leaf rows of a synced category tree, named by the path the tree stored. */
export function catalogLeafChoices(payload: { coverage?: string; nodes?: CatalogCategoryNode[] } | undefined): CategoryChoice[] {
  if (!payload || payload.coverage !== "tree" || !Array.isArray(payload.nodes)) return [];
  return payload.nodes
    .filter((node) => node.is_leaf || node.has_children === false)
    .flatMap((node) => (node.path && node.id ? [{ label: node.path, id: node.id }] : []));
}
