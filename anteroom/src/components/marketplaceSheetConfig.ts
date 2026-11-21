import type { MissingField, NativeMarketplace } from "../api/nativeMarketplaces";
import type { ListingData, ListingSheetCell, ListingSheetRow } from "../api/types";
import { formFieldText, marketplaceDisplayValue } from "../listingPaths";
import { formatMoney } from "./analyticsFormat";
import { marketplaceCell, type MarketplaceCell } from "./inventoryTable";
import { independentFieldKey } from "./nativeFieldCommit";
import type { Listing } from "./sidebar/listingShelves";

export type SheetCellKind = "category" | "select" | "multi" | "number" | "text" | "textarea" | "complex";

export interface SheetColumn {
  key: string;
  label: string;
  width: string;
  align?: "left" | "right";
  kind: SheetCellKind;
  /** Shown as a column in the desktop/iPad spreadsheet grid (false = Quick Edit sheet only unless missing). */
  inGrid: boolean;
}

export const MARKETPLACE_SHEET_COLUMNS: Record<NativeMarketplace, readonly SheetColumn[]> = {
  depop: [
    { key: "direct_category_ids.depop", label: "Category", width: "12rem", kind: "category", inGrid: true },
    { key: "depop_specifics.brand", label: "Brand", width: "9rem", kind: "select", inGrid: true },
    { key: "depop_specifics.condition", label: "Condition", width: "9rem", kind: "select", inGrid: true },
    { key: "depop_specifics.size", label: "Size", width: "6.5rem", kind: "select", inGrid: true },
    { key: "depop_specifics.parcelSize", label: "Package size", width: "9.5rem", kind: "select", inGrid: true },
    { key: "price", label: "Price", width: "6rem", align: "right", kind: "number", inGrid: true },
    { key: "depop_specifics.colour", label: "Color", width: "8.5rem", kind: "multi", inGrid: true },
    { key: "depop_specifics.description", label: "Description", width: "14rem", kind: "textarea", inGrid: false },
  ],
  poshmark: [
    { key: "direct_category_ids.poshmark", label: "Category", width: "12rem", kind: "category", inGrid: true },
    { key: "poshmark_specifics.brand", label: "Brand", width: "9rem", kind: "text", inGrid: true },
    { key: "poshmark_specifics.condition", label: "Condition", width: "9rem", kind: "select", inGrid: true },
    { key: "poshmark_specifics.inventoryRows", label: "Size", width: "8rem", kind: "complex", inGrid: true },
    { key: "poshmark_specifics.colors", label: "Color", width: "8.5rem", kind: "multi", inGrid: true },
    { key: "price", label: "Price", width: "6rem", align: "right", kind: "number", inGrid: true },
    { key: "poshmark_specifics.title", label: "Title", width: "12rem", kind: "text", inGrid: false },
    { key: "poshmark_specifics.description", label: "Description", width: "14rem", kind: "textarea", inGrid: false },
  ],
  mercari: [
    { key: "direct_category_ids.mercari", label: "Category", width: "12rem", kind: "category", inGrid: true },
    { key: "mercari_specifics.brand", label: "Brand", width: "9rem", kind: "select", inGrid: true },
    { key: "mercari_specifics.condition", label: "Condition", width: "9rem", kind: "select", inGrid: true },
    { key: "mercari_specifics.nativeSizeId", label: "Size", width: "7rem", kind: "select", inGrid: true },
    { key: "mercari_specifics.shippingClassId", label: "Shipping", width: "10rem", kind: "complex", inGrid: true },
    { key: "price", label: "Price", width: "6rem", align: "right", kind: "number", inGrid: true },
    { key: "mercari_specifics.title", label: "Title", width: "12rem", kind: "text", inGrid: false },
    { key: "mercari_specifics.description", label: "Description", width: "14rem", kind: "textarea", inGrid: false },
  ],
  ebay: [
    { key: "direct_category_ids.ebay", label: "Category", width: "13rem", kind: "category", inGrid: true },
    { key: "ebay_specifics.condition", label: "Condition", width: "10rem", kind: "select", inGrid: true },
    { key: "ebay_specifics.Brand", label: "Brand", width: "9rem", kind: "select", inGrid: true },
    { key: "ebay_specifics.Size", label: "Size", width: "7rem", kind: "select", inGrid: true },
    { key: "price", label: "Price", width: "6.5rem", align: "right", kind: "number", inGrid: true },
    { key: "quantity", label: "Qty", width: "4.5rem", align: "right", kind: "number", inGrid: true },
    { key: "ebay_specifics.title", label: "Title", width: "12rem", kind: "text", inGrid: false },
  ],
  etsy: [
    { key: "direct_category_ids.etsy", label: "Category", width: "13rem", kind: "category", inGrid: true },
    { key: "etsy_specifics.whenMade", label: "When made", width: "9.5rem", kind: "select", inGrid: true },
    { key: "etsy_specifics.whoMade", label: "Who made it", width: "9.5rem", kind: "select", inGrid: true },
    { key: "etsy_specifics.whatIsIt", label: "What is it", width: "9rem", kind: "select", inGrid: true },
    { key: "price", label: "Price", width: "6rem", align: "right", kind: "number", inGrid: true },
    { key: "quantity", label: "Qty", width: "4.5rem", align: "right", kind: "number", inGrid: true },
    { key: "etsy_specifics.title", label: "Title", width: "12rem", kind: "text", inGrid: false },
  ],
};

export function gridColumnsForMarketplace(marketplace: NativeMarketplace): SheetColumn[] {
  return MARKETPLACE_SHEET_COLUMNS[marketplace].filter((col) => col.inGrid);
}

/** Shorten a breadcrumb path (`A > B > C` or `A / B / C`) to its leaf for compact grid cells. */
export function shortCategoryLabel(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "";
  const parts = trimmed.split(/\s*(?:>|\/)\s*/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : trimmed;
}

/** Format a cell value for compact display inside the spreadsheet grid. */
export function formatSheetCellValue(column: SheetColumn, cell: ListingSheetCell | undefined): string {
  const raw = cell?.value?.trim() ?? "";
  if (!raw) return "";
  if (column.key === "price") {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? formatMoney(parsed) : raw;
  }
  if (column.kind === "category") return shortCategoryLabel(raw);
  return raw;
}

/**
 * Format the marketplace-specific price shown in rows and List confirmations.
 * Preserves non-numeric eBay variation/auction labels when price is not a single number.
 */
export function sheetPriceLabel(
  marketplace: NativeMarketplace,
  sheetRow: ListingSheetRow | undefined,
  docListing?: ListingData | null,
  fallbackPrice?: number | null,
  docRevisionId?: string | null,
): string | null {
  const sheetRowMatchesDoc =
    !docListing
    || !docRevisionId
    || !sheetRow?.revision_id
    || sheetRow.revision_id === docRevisionId;
  const priceCell = sheetRowMatchesDoc ? sheetRow?.cells?.price : undefined;
  const cellText = priceCell?.value?.trim() ?? "";
  if (priceCell?.editable === false && cellText) {
    return cellText;
  }
  if (docListing) {
    const priceKey = independentFieldKey(marketplace, "price");
    const raw = marketplaceDisplayValue(docListing, priceKey, { tab: marketplace });
    const text = formFieldText(raw).trim();
    if (text) {
      const parsed = Number(text);
      return Number.isFinite(parsed) ? formatMoney(parsed) : text;
    }
  }
  if (cellText) {
    const parsed = Number(cellText);
    return Number.isFinite(parsed) ? formatMoney(parsed) : cellText;
  }
  if (fallbackPrice != null && Number.isFinite(fallbackPrice)) {
    return formatMoney(fallbackPrice);
  }
  return null;
}

/** Single shared rule across desktop and phone for whether the row can start a 2-step List. */
export function canListFromSheet(
  listing: Listing | null | undefined,
  status: MarketplaceCell | null | undefined,
  sheetRow: ListingSheetRow | undefined,
  canAct: boolean,
  revisionId?: string | null,
): boolean {
  if (!listing || listing.settled_at || !canAct || !status) return false;
  if (!sheetRow || !sheetRow.ready) return false;
  const rowRev = sheetRow.revision_id?.trim() ?? "";
  const passedRev = revisionId?.trim() ?? "";
  if (passedRev && rowRev && passedRev !== rowRev) return false;
  const effectiveRevision = passedRev || rowRev;
  if (!effectiveRevision) return false;
  return status.kind === "not_listed" || status.kind === "draft" || status.kind === "delisted";
}

/**
 * Order Quick Edit fields for phone scanning:
 * 1. Rejected fields first
 * 2. Missing required fields next
 * 3. Remaining columns in marketplace order
 */
export function orderedQuickEditColumns(
  marketplace: NativeMarketplace,
  sheetRow: ListingSheetRow | undefined,
): SheetColumn[] {
  const all = MARKETPLACE_SHEET_COLUMNS[marketplace];
  const rank = (col: SheetColumn): number => {
    const cell = sheetRow?.cells?.[col.key];
    if (cell?.rejected) return 0;
    if (cell?.missing) return 1;
    if (col.inGrid) return 2;
    return 3;
  };
  return [...all].sort((a, b) => rank(a) - rank(b));
}

export interface ScanSummary {
  /** Emphasized leading problem text when something needs fixing, else null. */
  alert: string | null;
  alertTone: "error" | "warning" | null;
  /** Quiet secondary attributes shown after the alert (or alone when ready). */
  meta: string;
}

/** One scannable summary line for a phone row: rejected/missing first, then filled highlights. */
export function buildScanSummary(
  marketplace: NativeMarketplace,
  sheetRow: ListingSheetRow | undefined,
): ScanSummary {
  const missing = sheetRow?.missing ?? [];
  const rejected = missing.filter((item) => item.rejected);
  const missingOnly = missing.filter((item) => !item.rejected);

  let alert: string | null = null;
  let alertTone: "error" | "warning" | null = null;
  if (rejected.length > 0) {
    alert = `Fix ${rejected.slice(0, 2).map((item) => item.label).join(", ")}`;
    alertTone = "error";
  } else if (missingOnly.length > 0) {
    const shown = missingOnly.slice(0, 2).map((item) => item.label).join(", ");
    alert = missingOnly.length > 2 ? `Missing ${shown} +${missingOnly.length - 2}` : `Missing ${shown}`;
    alertTone = "warning";
  }

  const columns = gridColumnsForMarketplace(marketplace).filter((col) => col.key !== "price");
  const highlights: string[] = [];
  for (const col of columns) {
    const cell = sheetRow?.cells?.[col.key];
    if (!cell || cell.missing || cell.rejected) continue;
    const text = formatSheetCellValue(col, cell);
    if (text) highlights.push(text);
    if (highlights.length >= (alert ? 2 : 3)) break;
  }

  return {
    alert,
    alertTone,
    meta: highlights.join(" · "),
  };
}

/** Find the next un-listed row after `currentId` for one-handed `Next draft →` traversal. */
export function nextQuickEditListingId(
  rows: readonly Listing[],
  currentId: string,
  marketplace: NativeMarketplace,
): string | null {
  if (rows.length <= 1) return null;
  const start = rows.findIndex((row) => row.id === currentId);
  if (start < 0) return null;
  const isCandidate = (row: Listing) => {
    if (row.settled_at) return false;
    const status = marketplaceCell(row, marketplace);
    return status.kind !== "listed" && status.kind !== "sold" && status.kind !== "working";
  };
  for (let offset = 1; offset < rows.length; offset += 1) {
    const candidate = rows[(start + offset) % rows.length];
    if (isCandidate(candidate)) return candidate.id;
  }
  return null;
}

/** Adapt a sheet row's missing items to the MissingField shape expected by marketplaceCell(). */
export function sheetMissingFields(sheetRow: ListingSheetRow | undefined): MissingField[] | undefined {
  if (!sheetRow) return undefined;
  return (sheetRow.missing ?? []).map((item) => ({
    field: item.field,
    label: item.label,
    message: item.message ?? "",
  }));
}
