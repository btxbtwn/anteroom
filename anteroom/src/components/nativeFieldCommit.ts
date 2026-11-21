import editorFields from "../../../skills/list-this/references/listing-editor-fields.json";
import type { ListingData } from "../api/types";
import { cloneListing, setListingEditorValue } from "../listingPaths";
import { agentFieldLocked } from "./agentFieldLocks";

/** Per-marketplace price paths (`depop_specifics.price`, `poshmark_specifics.poshmarkPrice`, etc.). */
export const MARKETPLACE_PRICE_KEYS: Record<string, string> = editorFields.marketplace_price_keys;

/** General fields that each marketplace form follows until edited on that marketplace. */
export const FOLLOWED_GENERAL: ReadonlySet<string> = new Set(editorFields.followed_general);

/** General fills these. A marketplace edit is stored on that form alone. */
export function independentFieldKey(tab: string, key: string): string {
  if (tab === "general" || key.includes(".")) return key;
  if (key === "price") return MARKETPLACE_PRICE_KEYS[tab] ?? key;
  if (FOLLOWED_GENERAL.has(key)) return `${tab}_specifics.${key}`;
  return key;
}

/** True when a native form row is marked locked only because it inherits from General. */
export function isInheritedGeneralLock(locked: boolean | undefined, help: string | undefined): boolean {
  return Boolean(locked && /change it there/i.test(help || ""));
}

/** Minimal field metadata required to commit a marketplace or general field edit. */
export interface CommitFieldSpec {
  key: string;
  type?: string;
  locked?: boolean;
  optionValues?: Record<string, string>;
  clears?: readonly string[];
  defaultValue?: unknown;
}

export interface CommitNativeFieldOptions {
  also?: Record<string, string>;
  agentLocks?: readonly string[];
  disabled?: boolean;
}

/** Coerce a numeric field's text value the same way the listing editor saves it. */
export function coerceNumericValue(val: string): string | number | null {
  if (val === "") return null;
  if (!isNaN(Number(val)) && val.trim() !== "") return Number(val);
  return val;
}

/**
 * Apply one field edit to a cloned listing using the exact same rules as the
 * listing editor: option-value mapping, numeric coercion, companion paths,
 * Poshmark sizeSetId synchronization, and typed dependent-field clears.
 *
 * Returns `null` when the edit is blocked (disabled, locked, or invalid object string).
 */
export function commitNativeField(
  listing: ListingData,
  fields: readonly CommitFieldSpec[],
  key: string,
  value: string | string[] | boolean,
  options: CommitNativeFieldOptions = {},
): ListingData | null {
  const { also = {}, agentLocks = [], disabled = false } = options;
  const field = fields.find((entry) => entry.key === key);
  if (disabled || agentFieldLocked(key, agentLocks) || field?.locked) return null;
  if (typeof value === "string" && value.trim() === "[object Object]") return null;

  const updated = cloneListing(listing);
  const stored = typeof value === "string"
    ? field?.optionValues?.[value] ?? (field?.type === "number" ? coerceNumericValue(value) : value)
    : Array.isArray(value)
      ? value.map((label) => field?.optionValues?.[label] ?? label)
      : value;

  setListingEditorValue(updated, key, stored);
  for (const [path, extra] of Object.entries(also)) {
    setListingEditorValue(updated, path, extra);
  }
  if (key === "poshmark_specifics.nativeSizeId") {
    const sizeSetDefault = fields.find((row) => row.key === "poshmark_specifics.sizeSetId")?.defaultValue;
    setListingEditorValue(updated, "poshmark_specifics.sizeSetId", sizeSetDefault || "");
  }
  for (const dependent of field?.clears || []) {
    const dependentField = fields.find((entry) => entry.key === dependent);
    setListingEditorValue(
      updated,
      dependent,
      dependent.endsWith(".attributes")
        ? {}
        : dependent.endsWith(".inventoryRows")
          ? []
          : dependentField?.type === "boolean"
            ? false
            : "",
    );
  }
  return updated;
}

/** Reverse-map a stored option ID back to its display label when `optionValues` is present. */
export function optionLabelForValue(value: string, optionValues?: Record<string, string>): string {
  if (!value || !optionValues) return value;
  const match = Object.entries(optionValues).find(([, optionId]) => optionId === value);
  return match ? match[0] : value;
}
