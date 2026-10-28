/** A marketplace category the seller can save, named by its full path. */
export interface CategoryChoice {
  label: string;
  id: string;
}

export interface CategoryItem {
  /** The name shown in the dropdown. */
  label: string;
  /** Path from the top of the tree through this row. */
  path: string[];
  /** True when choosing this row is the last step. */
  terminal: boolean;
}

export interface CategoryMenu {
  items: CategoryItem[];
}

const SEPARATOR = /\s*(?:>|›|\|)\s*/;

/** Split a stored path on any separator the catalogs use (Sellwild writes "Group | Category"). */
export function categorySegments(label: string): string[] {
  return label.split(SEPARATOR).map((part) => part.trim()).filter(Boolean);
}

export function categoryPathKey(path: readonly string[]): string {
  return path.join(" > ");
}

function byLabel(a: { label: string }, b: { label: string }): number {
  return a.label.localeCompare(b.label);
}

function matchesPrefix(parts: readonly string[], prefix: readonly string[]): boolean {
  for (let index = 0; index < prefix.length; index += 1) {
    if (parts[index] !== prefix[index]) return false;
  }
  return true;
}

/**
 * The next dropdown for a path already chosen: one level of the tree, as the
 * marketplaces' own sell pages show it (Men / Women / Kids, then the group,
 * then the item). A row with categories under it is not terminal, so the
 * seller keeps going; a row that is itself a choice still saves when picked.
 * One pass: eBay's leaf list is large, and each keystroke must not rescan it.
 */
export function categoryMenu(choices: readonly CategoryChoice[], prefix: readonly string[]): CategoryMenu | null {
  const items = new Map<string, CategoryItem>();
  for (const choice of choices) {
    const parts = categorySegments(choice.label);
    if (parts.length <= prefix.length || !matchesPrefix(parts, prefix)) continue;
    const path = parts.slice(0, prefix.length + 1);
    const key = categoryPathKey(path);
    const deeper = parts.length > path.length;
    const item = items.get(key);
    if (item) item.terminal &&= !deeper;
    else items.set(key, { label: path[path.length - 1], path, terminal: !deeper });
  }
  if (!items.size) return null;
  return { items: [...items.values()].sort(byLabel) };
}

/** The choice whose path is exactly these segments. */
export function categoryChoiceAt(choices: readonly CategoryChoice[], path: readonly string[]): CategoryChoice | undefined {
  const key = categoryPathKey(path);
  return choices.find((choice) => categoryPathKey(categorySegments(choice.label)) === key);
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
  if (!named || !options.some((option) => SEPARATOR.test(option))) return null;
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
