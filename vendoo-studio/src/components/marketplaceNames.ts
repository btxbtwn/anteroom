/** How each marketplace is named in the app's own copy, badges and labels. */

export const MARKETPLACE_NAMES: Record<string, string> = {
  general: "General",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
  facebook: "Facebook",
  shopify: "Shopify",
  whatnot: "Whatnot",
  vestiaire: "Vestiaire Collective",
  other: "Other / in person",
};

/** A marketplace id as a person reads it; an unknown id is capitalised as-is. */
export function marketplaceName(id: string): string {
  return MARKETPLACE_NAMES[id] || id.charAt(0).toUpperCase() + id.slice(1);
}
