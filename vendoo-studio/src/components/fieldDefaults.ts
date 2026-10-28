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
