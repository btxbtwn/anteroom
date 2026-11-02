import { ChevronDownIcon, CircleAlertIcon, MessageSquareIcon, WrenchIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { useVendooEnabled } from "../vendooMode";
import { Button } from "./ui/button";
import { OPTIONS_RULE, optionsForField, type DropdownForms } from "../dropdownOptions";

type Blocker = { field?: string; message?: string };

export type BlockerField = {
  marketplace?: string;
  field?: string;
  expected?: unknown;
  observed?: unknown;
  error?: unknown;
  options?: string[];
};

function fieldPathToTarget(field?: string): { marketplace: string; field: string } | null {
  const raw = String(field || "").trim();
  if (!raw) return null;
  const specifics = raw.match(/^([a-z]+)_specifics\.(.+)$/i);
  if (specifics) {
    return { marketplace: specifics[1].toLowerCase(), field: specifics[2] };
  }
  if (raw.includes(".")) {
    const [head, ...rest] = raw.split(".");
    return { marketplace: head.toLowerCase() === "general" ? "general" : head.toLowerCase(), field: rest.join(".") };
  }
  return { marketplace: "general", field: raw };
}

function listingLabel(listingTitle?: string): string {
  return String(listingTitle || "").trim() || "this listing";
}

function compactBlockerFields(
  fields?: BlockerField[] | null,
  forms?: DropdownForms,
): BlockerField[] {
  if (!Array.isArray(fields)) return [];
  const seen = new Set<string>();
  const rows: BlockerField[] = [];
  for (const item of fields) {
    const marketplace = String(item?.marketplace || "").trim().toLowerCase();
    const field = String(item?.field || "").trim();
    if (!marketplace || !field) continue;
    const key = `${marketplace}:${field.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      marketplace,
      field,
      expected: item.expected,
      observed: item.observed,
      error: item.error,
      // The whole dropdown list, not a sample: a truncated list just invites
      // the model to answer with the invalid value again.
      options: Array.isArray(item.options) && item.options.length
        ? item.options.slice(0, 120)
        : optionsForField(forms, marketplace, field)?.slice(0, 120),
    });
    if (rows.length >= 40) break;
  }
  return rows;
}

function parseFieldsFromErrorText(errorText: string): BlockerField[] {
  const text = String(errorText || "");
  const markets = "general|ebay|etsy|poshmark|mercari|depop";
  const re = new RegExp(`\\b(${markets})\\s*\\/\\s*([^,;]+?)(?=\\s*,\\s*(?:${markets})\\b|\\s*\\.\\s|$)`, "gi");
  const rows: BlockerField[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(re)) {
    const marketplace = String(match[1] || "").trim().toLowerCase();
    const field = String(match[2] || "").trim().replace(/\s+/g, " ");
    if (!marketplace || !field) continue;
    const key = `${marketplace}:${field.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ marketplace, field });
  }
  return rows;
}

function missingFieldsJsonExamples(fields: BlockerField[]): string {
  const examples = fields.slice(0, 4).map((item) => {
    const marketplace = item.marketplace || "ebay";
    const field = item.field || "Brand";
    return `{"marketplace":"${marketplace}","field":"${field}","value":"..."}`;
  });
  return examples.join(",") || '{"marketplace":"ebay","field":"Brand","value":"..."}';
}

function fieldLines(fields: BlockerField[]): string {
  return fields
    .map((item) => {
      const bits = [`- ${item.marketplace} / ${item.field}`];
      if (item.expected != null && String(item.expected).trim()) bits.push(`  expected: ${String(item.expected)}`);
      if (item.observed != null && String(item.observed).trim()) bits.push(`  observed: ${String(item.observed)}`);
      if (item.error != null && String(item.error).trim()) bits.push(`  error: ${String(item.error)}`);
      if (item.options?.length) {
        bits.push(`  options (use only these, verbatim): ${item.options.join(" | ")}`);
      }
      return bits.join("\n");
    })
    .join("\n");
}

function replyShape(fields: BlockerField[]): string {
  return `Reply with JSON in this exact shape:

\`\`\`json
{"missing_fields":[${missingFieldsJsonExamples(fields)}]}
\`\`\`

Use marketplace ids and field names exactly as listed. ${OPTIONS_RULE} Anteroom saves the listing JSON from this reply; filling the live Vendoo draft is a separate step. Do not publish.
`;
}

export function validationErrorsPrompt(
  blockers: Blocker[],
  listingTitle?: string,
  forms?: DropdownForms,
): string {
  const title = listingLabel(listingTitle);
  const lines = blockers
    .filter((item) => item.message)
    .map((item) => {
      const target = fieldPathToTarget(item.field);
      const where = target
        ? `${target.marketplace} / ${target.field}`
        : (item.field || "listing");
      return `- ${where}: ${item.message}`;
    });
  const fields = compactBlockerFields(
    blockers
      .map((item) => fieldPathToTarget(item.field))
      .filter((target): target is { marketplace: string; field: string } => Boolean(target))
      .map((target) => ({ marketplace: target.marketplace, field: target.field })),
    forms,
  );
  const examples = fields
    .slice(0, 3)
    .map((target) => `{"marketplace":"${target.marketplace}","field":"${target.field}","value":"..."}`);

  return `Fix these Anteroom validation errors so Send to Vendoo can proceed for "${title}".

Update EVERY field listed below so the errors clear. Use photo analysis and seller notes. Do not invent unsupported facts.
For eBay Season intelligently choose exactly one of Spring, Summer, Fall, or Winter from the item (title, fabric, type, photos). Never leave it blank.
For every marketplace field shown after Show Optional Fields (eBay, Etsy, Depop, and others): fill a real value when it pertains to the item. When it does not apply, send it with an empty value and "status": "not_applicable" — never write "Does Not Apply".
${OPTIONS_RULE}
Required fields must not be cleared to resolve an error. Reuse supported values from the current listing. For an unidentified brand, use Unbranded; include that brand before the size in the title. Copy the listing's department into eBay department. If captured department options exclude it, explain the category/schema mismatch rather than choose an inaccurate department. Set Poshmark original price to 0.

Validation errors:
${lines.join("\n") || "- (no details)"}

${fields.length ? `Fields to fill:\n${fieldLines(fields)}\n\n` : ""}Reply with JSON in this exact shape:

\`\`\`json
{"missing_fields":[${examples.join(",") || '{"marketplace":"ebay","field":"Season","value":"..."}'}]}
\`\`\`

Use marketplace ids and field names that match the errors. Anteroom saves the listing JSON from this reply; filling the live Vendoo draft is a separate step.
`;
}

export function jobErrorPrompt(
  errorText: string,
  listingTitle?: string,
  blockerFields?: BlockerField[] | null,
  forms?: DropdownForms,
): string {
  const title = listingLabel(listingTitle);
  const detail = String(errorText || "").trim() || "(no details)";
  const fields = (() => {
    const fromPayload = compactBlockerFields(blockerFields, forms);
    return fromPayload.length
      ? fromPayload
      : compactBlockerFields(parseFieldsFromErrorText(detail), forms);
  })();
  if (fields.length) {
    return `Fix this Anteroom job / verification error for "${title}".

Job error:
${detail}

Generate values for EVERY field listed below from photos and seller notes — both the fields named in the error and any other missed fields included here. Do not rewrite unrelated fields.
${OPTIONS_RULE}

Fields:
${fieldLines(fields)}

${replyShape(fields)}`;
  }
  return `Fix this Anteroom job / verification error for "${title}".

Job error:
${detail}

From photos and seller notes, fill what you can. For fields you cannot support, say so clearly.
Prefer updating listing values with JSON in this shape:

\`\`\`json
{"missing_fields":[{"marketplace":"ebay","field":"Brand","value":"..."}]}
\`\`\`

Use marketplace ids and field names from the error. Do not publish.
`;
}

export function emptyFieldsButtonLabel(count: number): string {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (n <= 0) return "Fill empty fields";
  return `Fill ${n} empty field${n === 1 ? "" : "s"}`;
}

export function CopyableLlmError({
  text,
  prompt,
  onAskChat,
  emptyFieldsCount = 0,
  emptyFieldsPrompt,
  hideDetails = false,
  issueCount = 0,
  className,
}: {
  text: string;
  prompt: string;
  onAskChat?: (text: string) => void;
  emptyFieldsCount?: number;
  emptyFieldsPrompt?: string;
  /** The caller already lists these issues, so the raw dump would repeat them. */
  hideDetails?: boolean;
  /** Blocking issues, when known, so Fix says how many. */
  issueCount?: number;
  className?: string;
}) {
  const vendooOn = useVendooEnabled();
  const body = String(text || "").trim();
  const emptyCount = Math.max(0, Math.floor(Number(emptyFieldsCount) || 0));
  const canFillEmpty = Boolean(onAskChat && emptyFieldsPrompt && emptyCount > 0);
  // No error and nothing left to fill — nothing to show. With fields still
  // empty the card stays so its fill button survives the error clearing.
  if (!body && !canFillEmpty) return null;

  const fixLabel = issueCount > 0
    ? `Fix ${issueCount} in chat`
    : "Fix in chat";

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      {onAskChat && (body || canFillEmpty) && (
        <div className="flex flex-wrap gap-1.5 max-[900px]:[&>*]:w-full">
          {body && (
            <Button
              variant="outline"
              size="xs"
              title={vendooOn
                ? "Send the blocking issues to chat. Chat updates this listing. It does not send to Vendoo."
                : "Send the blocking issues to chat. Chat updates this listing."}
              onClick={() => onAskChat(prompt)}
            >
              <WrenchIcon />
              {fixLabel}
            </Button>
          )}
          {canFillEmpty && (
            <Button
              variant="outline"
              size="xs"
              title={vendooOn
                ? "Send empty listing fields to chat. Does not change Vendoo yet."
                : "Send empty listing fields to chat."}
              onClick={() => {
                if (!emptyFieldsPrompt) return;
                onAskChat(emptyFieldsPrompt);
              }}
            >
              <MessageSquareIcon />
              {emptyFieldsButtonLabel(emptyCount)}
            </Button>
          )}
        </div>
      )}
      {body && !hideDetails && (
        <details className="group rounded-lg border border-error/32 bg-error-surface">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-error-foreground text-xs [&::-webkit-details-marker]:hidden">
            <CircleAlertIcon className="size-3.5 shrink-0 text-error" aria-hidden="true" />
            Errors need attention
            <ChevronDownIcon className="ms-auto size-3.5 shrink-0 opacity-70 transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words border-error/20 border-t px-2.5 py-2 text-error-foreground/90 text-xs max-[900px]:max-h-22">
            {body}
          </div>
        </details>
      )}
    </div>
  );
}
