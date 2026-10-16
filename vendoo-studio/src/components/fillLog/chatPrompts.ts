import type { FillLogEntry } from "../../api/types";
import { OPTIONS_RULE, optionsForField, type DropdownForms } from "../../dropdownOptions";
import { fieldMatchKey, normalizeFieldName } from "./fieldKeys";
import { applicableChatFailures, fieldsNeedingListingValues } from "./fieldSync";
import { EMPTY_CELL, listingTextForField, listingValueForField } from "./fieldValues";
import type { DraftField, DraftForm } from "./formModel";

/** Keep the option dump readable when a dropdown has hundreds of entries. */
const MAX_PROMPT_OPTIONS = 60;

export function leftoverGeneratedValue(
  listing: Record<string, unknown> | undefined,
  entry: FillLogEntry,
  field?: DraftField,
): string {
  return (
    listingValueForField(
      listing,
      entry.marketplace,
      field || { key: entry.field, label: entry.field, value: "", missing: true },
    ) || String(entry.value_preview || "").trim()
  );
}

function listingTitle(listing?: Record<string, unknown>): string {
  const title = String(listing?.title || "").trim();
  return title || "(untitled listing)";
}

function leftoverStatusLabel(entry: FillLogEntry): string {
  if (entry.status === "invalid") return "invalid-dropdown";
  const reason = String(entry.reason || "").toLowerCase();
  if (/invalid.*dropdown|not a valid option|no matching option|option not found|dropdown/.test(reason) && entry.status !== "filled") {
    return "invalid-dropdown";
  }
  if (/^no evidence\b/i.test(String(entry.reason || "").trim())) return "No evidence";
  return entry.status;
}

/** Vendoo's dropdown list for a field, so chat answers with a value the form takes. */
function optionsLine(
  dropdowns: DropdownForms | undefined,
  marketplace: string,
  field: string,
): string {
  const options = optionsForField(dropdowns, marketplace, field);
  if (!options?.length) return "";
  const shown = options.slice(0, MAX_PROMPT_OPTIONS);
  const rest = options.length - shown.length;
  return `\n  Options (use only these, verbatim): ${shown.join(" | ")}${rest > 0 ? ` (+${rest} more)` : ""}`;
}

export function leftoverFieldPrompt(
  listing: Record<string, unknown> | undefined,
  entry: FillLogEntry,
  currentValue: string,
  dropdowns?: DropdownForms,
): string {
  const title = listingTitle(listing);
  const current = String(currentValue || entry.value_preview || "").trim() || "(empty)";
  const reason = String(entry.reason || "").trim() || "(none)";
  return `Fix this Vendoo field for listing "${title}".

Listing: ${title}
Marketplace: ${entry.marketplace}
Field: ${entry.field}${optionsLine(dropdowns, entry.marketplace, entry.field)}
Current value: ${current}
Status: ${leftoverStatusLabel(entry)}
Failure reason: ${reason}

Generate a value for ONLY this field from the photos and current listing. Do not rewrite unrelated fields.

${OPTIONS_RULE}

Reply with JSON in this exact shape:

\`\`\`json
{"missing_fields":[{"marketplace":"${entry.marketplace}","field":"${entry.field}","value":"..."}]}
\`\`\`
`;
}

export function emptyFieldsPrompt(
  forms: DraftForm[],
  fromDraft: boolean,
  listing?: Record<string, unknown>,
  dropdowns?: DropdownForms,
): string {
  const title = listingTitle(listing);
  const rows: {
    marketplace: string;
    form: string;
    field: string;
    options: string;
    current: string;
    status: string;
    reason: string;
  }[] = [];
  for (const { form, field } of fieldsNeedingListingValues(forms, listing, fromDraft)) {
    const leftover = field.leftover;
    rows.push({
      marketplace: form.id,
      form: form.label,
      field: field.label,
      options: optionsLine(dropdowns, form.id, field.key) || optionsLine(dropdowns, form.id, field.label),
      current: listingTextForField(listing, form.id, field) || EMPTY_CELL,
      status: leftover ? leftoverStatusLabel(leftover) : "missing in listing",
      reason: leftover?.reason || (fromDraft
        ? "empty on Vendoo and in listing JSON"
        : "empty in listing JSON"),
    });
  }
  const limited = rows.slice(0, 50);
  const lines = limited.map((row) => `- Listing: ${title}
  Marketplace: ${row.marketplace}
  Field: ${row.field}${row.options}
  Current value: ${row.current}
  Status: ${row.status}
  Reason: ${row.reason}`);
  const intro = fromDraft
    ? `These fields are empty in the listing JSON for "${title}". Generate values for ONLY these fields from the photos and current listing. Do not rewrite the rest of the listing.`
    : `These listing fields are still empty on listing "${title}". Generate values for ONLY these fields from the photos and current listing. Do not rewrite the rest of the listing.`;
  return `${intro}

Reply with JSON in this exact shape:

\`\`\`json
{"missing_fields":[{"marketplace":"general","field":"SKU","value":"..."}]}
\`\`\`

Use the marketplace ids and field names exactly as listed. Studio updates the listing JSON and Forms/Fields UI when this reply finishes; filling the live Vendoo draft is a separate step.

${OPTIONS_RULE}

When the item's brand is not offered by a marketplace, answer "Other" for Depop and "No Brand/Not sure" for Mercari — never substitute a different brand.

Empty fields:
${lines.join("\n")}`;
}

/** One Ask-chat prompt for empty listing values and Apply/Send failures (deduped). */
export function askChatGapsPrompt(
  forms: DraftForm[],
  fromDraft: boolean,
  listing: Record<string, unknown> | undefined,
  failures: FillLogEntry[],
  dropdowns?: DropdownForms,
): string {
  const title = listingTitle(listing);
  const seen = new Set<string>();
  const lines: string[] = [];

  const pushLine = (line: string, key: string) => {
    if (seen.has(key) || lines.length >= 50) return;
    seen.add(key);
    lines.push(line);
  };

  for (const entry of applicableChatFailures(forms, listing, failures)) {
    const marketplace = String(entry.marketplace || "").toLowerCase();
    const field = entry.field || "";
    const key = `${marketplace}:${normalizeFieldName(field)}`;
    const current = leftoverGeneratedValue(listing, entry) || "(empty)";
    const reason = String(entry.reason || "").trim() || "(none)";
    pushLine(
      `- Listing: ${title}
  Marketplace: ${entry.marketplace}
  Field: ${field}${optionsLine(dropdowns, entry.marketplace, field)}
  Current value: ${current}
  Status: ${leftoverStatusLabel(entry)}
  Reason: ${reason}`,
      key,
    );
  }

  for (const { form, field } of fieldsNeedingListingValues(forms, listing, fromDraft)) {
    const leftover = field.leftover;
    const key = `${form.id}:${fieldMatchKey(field)}`;
    pushLine(
      `- Listing: ${title}
  Marketplace: ${form.id}
  Field: ${field.label}${optionsLine(dropdowns, form.id, field.key) || optionsLine(dropdowns, form.id, field.label)}
  Current value: ${listingTextForField(listing, form.id, field) || EMPTY_CELL}
  Status: ${leftover ? leftoverStatusLabel(leftover) : "missing in listing"}
  Reason: ${leftover?.reason || (fromDraft
    ? "empty on Vendoo and in listing JSON"
    : "empty in listing JSON")}`,
      key,
    );
  }

  const emptyCount = fieldsNeedingListingValues(forms, listing, fromDraft).length;
  const failCount = applicableChatFailures(forms, listing, failures).length;
  const parts: string[] = [];
  if (emptyCount) parts.push(`${emptyCount} empty`);
  if (failCount) parts.push(`${failCount} failed`);
  const mix = parts.join(" + ") || "listed";

  return `Fill these listing fields for "${title}" (${mix}). Generate values for ONLY these fields from the photos and current listing. Do not rewrite unrelated fields.

Reply with JSON in this exact shape:

\`\`\`json
{"missing_fields":[{"marketplace":"ebay","field":"Brand","value":"..."}]}
\`\`\`

Use the marketplace ids and field names exactly as listed. Studio updates the listing JSON and Forms/Fields UI when this reply finishes; filling the live Vendoo draft is a separate step.

${OPTIONS_RULE}

When the item's brand is not offered by a marketplace, answer "Other" for Depop and "No Brand/Not sure" for Mercari — never substitute a different brand.

Fields:
${lines.join("\n") || "- (none)"}`;
}
