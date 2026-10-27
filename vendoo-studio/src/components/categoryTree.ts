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

export interface CategoryGroup {
  heading: string;
  items: CategoryItem[];
}

export interface CategoryMenu {
  groups: CategoryGroup[];
}

export interface CategoryStep {
  menu: CategoryMenu;
  /** The path key of the row selected at this step, or empty. */
  value: string;
}

const SEPARATOR = /\s*(?:>|›)\s*/;

/** Split a stored path on either separator the catalogs use. */
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
 * The next dropdown for a path already chosen.
 * A group heading is the category those rows belong to. A row that still has
 * categories under it is not terminal, so the seller can keep going.
 * One pass: eBay's leaf list is large, and each keystroke must not rescan it.
 */
export function categoryMenu(choices: readonly CategoryChoice[], prefix: readonly string[]): CategoryMenu | null {
  const rows: string[][] = [];
  let deeper = false;
  for (const choice of choices) {
    const parts = categorySegments(choice.label);
    if (parts.length <= prefix.length || !matchesPrefix(parts, prefix)) continue;
    rows.push(parts);
    if (parts.length > prefix.length + 1) deeper = true;
  }
  if (!rows.length) return null;
  if (!deeper) {
    const heading = prefix[prefix.length - 1] || "Category";
    const seen = new Set<string>();
    const items: CategoryItem[] = [];
    for (const parts of rows) {
      const key = categoryPathKey(parts);
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({ label: parts[prefix.length] || key, path: parts, terminal: true });
    }
    items.sort(byLabel);
    return { groups: [{ heading, items }] };
  }
  const groups = new Map<string, Map<string, CategoryItem>>();
  const extended = new Set<string>();
  for (const parts of rows) {
    const heading = parts[prefix.length];
    let items = groups.get(heading);
    if (!items) {
      items = new Map();
      groups.set(heading, items);
    }
    if (parts.length === prefix.length + 1) {
      const key = categoryPathKey(parts);
      if (!items.has(key)) items.set(key, { label: heading, path: parts, terminal: true });
    } else {
      const path = parts.slice(0, prefix.length + 2);
      const key = categoryPathKey(path);
      if (parts.length > path.length) extended.add(key);
      if (!items.has(key)) items.set(key, { label: path[path.length - 1], path, terminal: true });
    }
  }
  for (const items of groups.values()) {
    for (const [key, item] of items) {
      if (extended.has(key)) item.terminal = false;
    }
  }
  return {
    groups: [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([heading, items]) => ({ heading, items: [...items.values()].sort(byLabel) })),
  };
}

/** Each dropdown from the top of the tree through the current selection. */
export function categorySteps(choices: readonly CategoryChoice[], selected: readonly string[]): CategoryStep[] {
  const steps: CategoryStep[] = [];
  let prefix: string[] = [];
  for (let guard = 0; guard < 8; guard += 1) {
    const menu = categoryMenu(choices, prefix);
    if (!menu) break;
    const match = menu.groups
      .flatMap((group) => group.items)
      .filter((item) => item.path.every((segment, index) => selected[index] === segment))
      .sort((a, b) => b.path.length - a.path.length)[0];
    steps.push({ menu, value: match ? categoryPathKey(match.path) : "" });
    if (!match || match.terminal) break;
    prefix = match.path;
  }
  return steps;
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
