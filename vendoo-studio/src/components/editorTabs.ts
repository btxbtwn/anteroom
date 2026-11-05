const FORM_ORDER = ["ebay", "poshmark", "mercari", "depop", "etsy"];
const DEFAULT_SELECTED = ["ebay", "etsy", "poshmark", "mercari", "depop"];

/** The editor's tabs: General, then each marketplace the seller lists to that this app has a form for. */
export function editorTabs(selected: readonly string[] | undefined, runtime: { hosted: boolean; marketplaceForms: ReadonlySet<string> }): string[] {
  const chosen = new Set(selected ?? DEFAULT_SELECTED);
  return ["general", ...FORM_ORDER.filter((id) => (!runtime.hosted || runtime.marketplaceForms.has(id)) && chosen.has(id))];
}
