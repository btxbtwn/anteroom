/** The inventory filter bar, sized for Studio's sidebar. */
import { labelColor, labelDotClass, type LabelColor } from "../labelColors";

import type { ReactNode } from "react";
import { CheckIcon, ChevronDownIcon, ListFilterIcon, XIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceLabel } from "./fillLog/marketplaces";
import { SIDEBAR_ROW } from "./sidebar/sidebarStyles";
import {
  Menu,
  MenuCheckboxItem,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuTrigger,
} from "./ui/menu";
import {
  DEFAULT_LISTING_FILTERS,
  LISTING_SORTS,
  LISTING_STATUS_LABELS,
  LISTING_STATUS_TABS,
  activeFilterCount,
  compactCount,
  cycleLabelFilter,
  labelFilterState,
  statusForSort,
  toggleValue,
  type ListingFilters as Filters,
  type ListingSortId,
} from "./inventoryFilters";

/** One narrowing, as a checkable menu row: check, glyph, name, count. */
export function FilterRow({
  checked,
  disabled,
  title,
  count,
  glyph,
  label,
  onSelect,
}: {
  checked: boolean;
  disabled?: boolean;
  title?: string;
  count: number;
  glyph: ReactNode;
  label: string;
  onSelect: () => void;
}) {
  return (
    <MenuCheckboxItem
      checked={checked}
      disabled={disabled}
      title={title}
      closeOnClick={false}
      onCheckedChange={onSelect}
    >
      <span className="flex min-w-0 items-center gap-2">
        {glyph}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{count}</span>
      </span>
    </MenuCheckboxItem>
  );
}

/**
 * A label row with three states: off, included (✓) and excluded (✗). One
 * click steps through them, so "Summer" and "not Consigned" can be on at once.
 */
export function LabelRow({ label, count, glyph, state, onSelect }: {
  label: string;
  count: number;
  glyph: ReactNode;
  state: "include" | "exclude" | "none";
  onSelect: () => void;
}) {
  const stateText = state === "include" ? "included" : state === "exclude" ? "excluded" : "not filtered";
  return (
    <MenuItem
      closeOnClick={false}
      aria-label={`${label}, ${count}: ${stateText}`}
      title={state === "none" ? "Click to include, again to exclude" : state === "include" ? "Included · click to exclude" : "Excluded · click to clear"}
      data-label-state={state}
      className="grid grid-cols-[1rem_minmax(0,1fr)] pe-4"
      onClick={onSelect}
    >
      <span className="col-start-1 flex items-center justify-center" aria-hidden="true">
        {state === "include" ? <CheckIcon className="size-4" /> : state === "exclude" ? <XIcon className="size-4 text-destructive-foreground" /> : null}
      </span>
      <span className="flex min-w-0 items-center gap-2">
        {glyph}
        <span className={cn("min-w-0 flex-1 truncate", state === "exclude" && "line-through decoration-muted-foreground")}>{label}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{count}</span>
      </span>
    </MenuItem>
  );
}

export function Dot({ tone = "muted" }: { tone?: "muted" | "warning" | "info" }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "mx-[3px] size-2 shrink-0 rounded-full",
        tone === "warning" ? "bg-warning" : tone === "info" ? "bg-primary" : "bg-muted-foreground/40",
      )}
    />
  );
}

export function ListingFilters({
  filters,
  counts,
  labels,
  labelCounts,
  labelColors,
  marketplaces,
  marketplaceCounts,
  notListedCount,
  unsentCount,
  missingSaleCount,
  inactiveCount = 0,
  priceDriftCount,
  missingSkuCount,
  staleCounts,
  onChange,
}: {
  filters: Filters;
  /** Listings per status tab; null while the inventory is still loading. */
  counts: Record<string, number> | null;
  labels: string[];
  /** Label counts, keyed lowercase the way `labelCounts` returns them. */
  labelCounts: Record<string, number>;
  /** Colors the seller gave labels in Settings. */
  labelColors?: Record<string, LabelColor>;
  marketplaces: string[];
  /** What each marketplace row would keep: listed there, or missing from there under Not listed. */
  marketplaceCounts: Record<string, number>;
  notListedCount: number;
  /** How many listings hold edits not yet sent to a marketplace. */
  unsentCount: number;
  /** How many listings have a sale missing details. */
  missingSaleCount: number;
  /** How many listings are still up somewhere but not for sale there. */
  inactiveCount?: number;
  /** How many listings drifted from the pricing rules; undefined while the rules are off. */
  priceDriftCount?: number;
  /** How many listings have no SKU; undefined while that warning is off. */
  missingSkuCount?: number;
  /** How many listings each staleness cutoff would keep, keyed by its days. */
  staleCounts: Record<number, number>;
  onChange: (next: Filters) => void;
}) {
  const chosen = activeFilterCount(filters);
  const sortLabel = LISTING_SORTS.find((option) => option.id === filters.sort)?.label ?? "Sort";

  return (
    <div className="flex shrink-0 flex-col gap-1.5 px-(--sidebar-content-inset) pb-2">
      {/* The tabs answer to the sidebar's own width, which the reader drags
          between 200px and 30vw: counts drop first, the selected one's last.
          Each tab starts at its own text's width, so a long label is not cut
          while a short one has room to spare. */}
      <div
        className="@container/tabs flex gap-0.5 rounded-[var(--control-radius)] bg-sidebar-control-surface p-0.5 ring-1 ring-sidebar-border ring-inset"
        role="tablist"
        aria-label="Listing status"
      >
        {LISTING_STATUS_TABS.map((status) => {
          const selected = filters.status === status;
          return (
            <button
              key={status}
              type="button"
              role="tab"
              aria-selected={selected}
              data-active={selected}
              className="group/tab relative flex h-6 max-[900px]:h-9 max-[900px]:after:absolute max-[900px]:after:inset-x-0 max-[900px]:after:-inset-y-1 min-w-0 flex-auto cursor-pointer items-center justify-center overflow-hidden rounded-md px-1 text-xs font-medium text-sidebar-muted-foreground outline-none transition-colors hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground data-[active=true]:shadow-xs/5"
              // A narrow sidebar drops the counts, so the tab itself carries one.
              title={counts ? `${LISTING_STATUS_LABELS[status]} · ${counts[status] || 0}` : LISTING_STATUS_LABELS[status]}
              onClick={() => onChange({ ...filters, status })}
            >
              <span className="flex min-w-0 items-baseline gap-1">
                <span className="min-w-0 truncate">{LISTING_STATUS_LABELS[status]}</span>
                {/* The space keeps the accessible name "Draft 4", not "Draft4"; flex layout hides it. */}
                {counts ? " " : null}
                {counts ? (
                  <span className="shrink-0 text-2xs text-sidebar-muted-foreground tabular-nums @max-[300px]/tabs:group-data-[active=false]/tab:hidden @max-[212px]/tabs:hidden">
                    {compactCount(counts[status] || 0)}
                  </span>
                ) : null}
              </span>
            </button>
          );
        })}
      </div>

      <div className="@container/filters flex items-center gap-1">
        <Menu>
          <MenuTrigger
            data-active={chosen > 0}
            className={cn(SIDEBAR_ROW, "h-7 max-[900px]:h-11 w-auto gap-1.5 px-2 text-xs data-[popup-open]:bg-sidebar-row-hover [&>svg]:size-3.5")}
          >
            <ListFilterIcon />
            <span>Filters</span>
            {chosen ? (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-3xs font-semibold text-primary-foreground tabular-nums">
                {chosen}
              </span>
            ) : null}
          </MenuTrigger>
          <MenuPopup align="start" className="max-h-[min(70vh,32rem)] min-w-56" aria-label="Filter listings">
            <MenuGroup>
              <MenuGroupLabel>{filters.notListed ? "Not listed on" : "Marketplaces"}</MenuGroupLabel>
              {marketplaces.length ? (
                marketplaces.map((id) => (
                  <FilterRow
                    key={id}
                    checked={filters.marketplaces.includes(id)}
                    count={marketplaceCounts[id] || 0}
                    glyph={<MarketplaceLogo id={id} label={marketplaceLabel(id)} size={14} />}
                    label={marketplaceLabel(id)}
                    onSelect={() => onChange({ ...filters, marketplaces: toggleValue(filters.marketplaces, id) })}
                  />
                ))
              ) : (
                <p className="px-2 py-1 text-xs text-muted-foreground">Nothing listed yet</p>
              )}
            </MenuGroup>
            <MenuSeparator />
            <FilterRow
              checked={filters.notListed}
              count={notListedCount}
              // "Not listed" asks the opposite question of the marketplace
              // rows, so it turns them around rather than fighting them.
              title="Listings no marketplace carries — pick marketplaces above to see what is missing from them"
              glyph={<Dot />}
              label="Not listed"
              // Turning it off drops the picks: a marketplace nothing sits
              // on has no row to untick once the question is "listed on".
              onSelect={() =>
                onChange(
                  filters.notListed
                    ? { ...filters, notListed: false, marketplaces: [] }
                    : { ...filters, notListed: true },
                )
              }
            />
            <FilterRow
              checked={filters.unsent}
              count={unsentCount}
              title="Edited in Anteroom since this listing was last saved"
              glyph={<Dot tone="info" />}
              label="Unsent edits"
              onSelect={() => onChange({ ...filters, unsent: !filters.unsent })}
            />
            <FilterRow
              checked={filters.missingSale}
              count={missingSaleCount}
              title="Sold listings whose sales records lack the price, fees, shipping, cost of goods or sale date"
              glyph={<Dot tone="warning" />}
              label="Missing sale details"
              onSelect={() => onChange({ ...filters, missingSale: !filters.missingSale })}
            />
            {inactiveCount || filters.inactive ? (
              <FilterRow
                checked={filters.inactive}
                count={inactiveCount}
                title="Still on a marketplace but not for sale there (deactivated, expired or Not For Sale)"
                glyph={<Dot />}
                label="Inactive"
                onSelect={() => onChange({ ...filters, inactive: !filters.inactive })}
              />
            ) : null}
            {priceDriftCount !== undefined || filters.priceDrift ? (
              <FilterRow
                checked={filters.priceDrift}
                count={priceDriftCount || 0}
                title="Marketplace prices that differ from your pricing rules"
                glyph={<Dot tone="warning" />}
                label="Price inconsistency"
                onSelect={() => onChange({ ...filters, priceDrift: !filters.priceDrift })}
              />
            ) : null}
            {missingSkuCount !== undefined || filters.missingSku ? (
              <FilterRow
                checked={filters.missingSku}
                count={missingSkuCount || 0}
                title="Listings without a SKU"
                glyph={<Dot tone="warning" />}
                label="Missing SKU"
                onSelect={() => onChange({ ...filters, missingSku: !filters.missingSku })}
              />
            ) : null}

            <MenuSeparator />
            <MenuGroup>
              <MenuGroupLabel>Stale since listed</MenuGroupLabel>
              {Object.keys(staleCounts).map(Number).sort((a, b) => a - b).map((days) => (
                <FilterRow
                  key={days}
                  checked={filters.staleDays === days}
                  count={staleCounts[days] || 0}
                  glyph={<Dot />}
                  label={`${days}+ days`}
                  // One cutoff at a time: picking the one already on turns it off.
                  onSelect={() => onChange({ ...filters, staleDays: filters.staleDays === days ? 0 : days })}
                />
              ))}
            </MenuGroup>

            {labels.length ? (
              <>
                <MenuSeparator />
                <MenuGroup>
                  <MenuGroupLabel>Labels · include ✓ or exclude ✗</MenuGroupLabel>
                  {labels.map((label) => (
                    <LabelRow
                      key={label}
                      state={labelFilterState(filters, label)}
                      count={labelCounts[label.toLowerCase()] || 0}
                      glyph={labelColor(labelColors, label) ? <span aria-hidden="true" className={cn("mx-[3px] size-2 shrink-0 rounded-full", labelDotClass(labelColor(labelColors, label)!))} /> : <Dot />}
                      label={label}
                      onSelect={() => onChange(cycleLabelFilter(filters, label))}
                    />
                  ))}
                </MenuGroup>
              </>
            ) : null}

            {chosen ? (
              <>
                <MenuSeparator />
                <MenuItem onClick={() => onChange({ ...DEFAULT_LISTING_FILTERS, status: filters.status, sort: filters.sort })}>
                  <XIcon />
                  Clear filters
                </MenuItem>
              </>
            ) : null}
          </MenuPopup>
        </Menu>

        <Menu>
          <MenuTrigger
            aria-label={`Sort listings: ${sortLabel}`}
            className={cn(SIDEBAR_ROW, "ml-auto h-7 max-[900px]:h-11 w-auto gap-1 px-2 text-xs data-[popup-open]:bg-sidebar-row-hover [&>svg]:size-3")}
          >
            <span className="truncate">{sortLabel}</span>
            <ChevronDownIcon />
          </MenuTrigger>
          <MenuPopup align="end" className="min-w-44">
            <MenuRadioGroup
              value={filters.sort}
              onValueChange={(value) => {
                const sort = value as ListingSortId;
                onChange({ ...filters, sort, status: statusForSort(sort, filters.status) });
              }}
            >
              {LISTING_SORTS.map((option) => (
                <MenuRadioItem key={option.id} value={option.id}>
                  {option.label}
                </MenuRadioItem>
              ))}
            </MenuRadioGroup>
          </MenuPopup>
        </Menu>
      </div>
    </div>
  );
}
