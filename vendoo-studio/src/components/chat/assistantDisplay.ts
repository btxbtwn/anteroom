export function isListingJson(text: string): boolean {
  return text.includes('"title"') && (text.includes('"description"') || text.includes('"price"'));
}

export function extractJson(text: string): string | null {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced) {
    const inner = fenced[1].trim();
    try {
      JSON.parse(inner);
      return inner;
    } catch {
      /* fall through */
    }
  }
  const stripped = text.trim();
  try {
    JSON.parse(stripped);
    return stripped;
  } catch {
    /* fall through */
  }
  const embeddedArray = stripped.match(/\[[\s\S]*\]/);
  if (embeddedArray) {
    try {
      const parsed = JSON.parse(embeddedArray[0]);
      if (Array.isArray(parsed) && parsed[0]?.op) return embeddedArray[0];
    } catch {
      /* fall through */
    }
  }
  const embedded = stripped.match(/\{[\s\S]*\}/);
  if (!embedded) return null;
  try {
    JSON.parse(embedded[0]);
    return embedded[0];
  } catch {
    return null;
  }
}

const PATCH_LABELS: Record<string, string> = {
  category_path: "Category",
  department: "Department",
  styleTags: "Style tags",
  primaryStoreCategory: "Store category",
  secondaryStoreCategory: "Secondary store category",
  primaryColor: "Primary color",
  secondaryColor: "Secondary color",
};

function humanKey(token: string): string {
  return token
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/-/g, " ")
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function pathLabel(path: string): string {
  const parts = (path || "").replace(/^\//, "").split("/").filter((part) => part && part !== "-" && !/^\d+$/.test(part));
  if (!parts.length) return "Field";
  let prefix = "";
  if (parts[0].endsWith("_specifics")) {
    const market = parts[0].slice(0, -"_specifics".length);
    prefix = market === "ebay" ? "eBay" : market.charAt(0).toUpperCase() + market.slice(1);
    parts.shift();
  }
  const key = parts[parts.length - 1] || "";
  const label = PATCH_LABELS[key] || humanKey(key);
  return prefix ? `${prefix} ${label}` : label;
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value == null) return "";
  return String(value);
}

function summarizeJsonPatch(ops: { op?: string; path?: string; value?: unknown }[]): string {
  const lines = ops.flatMap((op) => {
    const label = pathLabel(op.path || "");
    if (op.op === "remove") return [`Removed ${label}`];
    if (op.op === "replace" || op.op === "add") return [`${label}: ${displayValue(op.value)}`];
    return [];
  });
  if (!lines.length) return "Saved those changes to the listing.";
  if (lines.length === 1) return `Updated the listing — ${lines[0]}.`;
  return `Updated the listing:\n${lines.map((line) => `- ${line}`).join("\n")}`;
}

const MARKETPLACE_DISPLAY: Record<string, string> = {
  general: "Vendoo",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
  facebook: "Facebook",
  shopify: "Shopify",
};

function summarizeMissingFields(
  rows: { marketplace?: string; field?: string; value?: unknown }[],
): string {
  const lines = rows.flatMap((row) => {
    const field = String(row.field || "").trim();
    const value = displayValue(row.value).trim();
    if (!field || !value) return [];
    const market = String(row.marketplace || "general").trim().toLowerCase() || "general";
    const label = MARKETPLACE_DISPLAY[market] || humanKey(market);
    return [`${label} / ${field}: ${value}`];
  });
  if (!lines.length) return "Saved field values to the listing JSON.";
  if (lines.length === 1) return `Saved to the listing JSON — ${lines[0]}. Fill on Vendoo when ready.`;
  return `Saved to the listing JSON:\n${lines.map((line) => `- ${line}`).join("\n")}`;
}

function stripJsonPayloads(text: string): string {
  let visible = text.replace(/```(?:json)?\s*[\s\S]*?(```|$)/gi, "\n");
  visible = visible.replace(/\n\s*\[[\s\S]*$/, "\n").replace(/\n\s*\{[\s\S]*$/, "\n").trim();
  if (/^[[{]/.test(visible)) return "";
  return visible;
}

function looksLikeJsonStream(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.startsWith("[") || trimmed.startsWith("{") || /```json/i.test(text);
}

export function assistantDisplayText(text: string): string {
  const prose = stripJsonPayloads(text);
  if (prose) return prose;

  const json = extractJson(text);
  if (!json) return looksLikeJsonStream(text) ? "" : text.trim();
  try {
    const parsed = JSON.parse(json);
    if (Array.isArray(parsed) && parsed[0]?.op) return summarizeJsonPatch(parsed);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.missing_fields)) {
      return summarizeMissingFields(parsed.missing_fields);
    }
    if (parsed && typeof parsed === "object" && typeof parsed.message === "string" && parsed.message.trim()) {
      return parsed.message.trim();
    }
    if (isListingJson(json) || isListingJson(text)) {
      // The save summary that follows says what is still empty; claiming it
      // here too gave chat two answers that disagreed.
      return "Listing generated.";
    }
  } catch {
    /* ignore */
  }
  return looksLikeJsonStream(text) ? "" : text.trim();
}

export function isPhotoAnalysis(text: string): boolean {
  return (
    text.startsWith("Photo analysis") &&
    (/-\s*brand:/i.test(text) || /-\s*size:/i.test(text) || /-\s*color:/i.test(text))
  );
}

export function isCompResearch(text: string): boolean {
  return text.startsWith("Sold comps:");
}

export function isStreamError(text: string): boolean {
  return text.startsWith("Error:");
}
