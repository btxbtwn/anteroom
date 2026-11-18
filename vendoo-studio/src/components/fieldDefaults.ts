import { editorValuePresent } from "../listingPaths";

/**
 * Saved answers the seller wants every new listing to start with.
 *
 * A shipping label is saved as a choice id plus the label it showed. Mercari
 * reuses the same carrier under a different id for each package weight, so the
 * label is what decides whether a field already matches its default.
 */
export interface SavedListingDefault {
  value: string;
  label: string;
}

export type SavedListingDefaults = Record<string, SavedListingDefault>;

/** The saved default this value is already using, or null. */
export function savedDefaultFor(
  defaults: SavedListingDefaults | undefined,
  path: string,
  value: unknown,
  label: string,
): SavedListingDefault | null {
  const saved = defaults?.[path];
  if (!saved) return null;
  const stored = String(value ?? "");
  const shown = String(label ?? "");
  return (stored !== "" && saved.value === stored) || (shown !== "" && saved.label === shown)
    ? saved
    : null;
}

/**
 * The saved default a row shows while the listing has no answer of its own.
 *
 * The form's own value wins: it is what List sends, and for Mercari's label it
 * is the saved carrier already matched to this package's weight tier. A saved
 * choice that is not one of the row's current choices is a preference the
 * marketplace has not offered, so the row stays empty rather than show it.
 */
export function shownSavedDefault(
  field: { defaultValue?: unknown; selectionOnly?: boolean; options?: readonly string[] },
  saved: SavedListingDefault | undefined,
): string | undefined {
  if (!saved || editorValuePresent(field.defaultValue)) return undefined;
  if (field.selectionOnly && !(field.options ?? []).includes(saved.label)) return undefined;
  return saved.label;
}
