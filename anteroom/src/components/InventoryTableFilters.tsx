/** The inventory table's filter menu: labels to include or exclude, and the guardrail flags. */
import { ListFilterIcon, XIcon } from "lucide-react";
import { labelColor, labelDotClass, type LabelColor } from "../labelColors";
import { cn } from "../lib/utils";
import { FavoriteStar } from "./favorites";
import { Dot, FilterRow, LabelRow } from "./ListingFilters";
import {
  cycleLabelFilter,
  favoriteCount,
  inactiveCount,
  labelCounts,
  labelFilterState,
  labelOptions,
  missingSkuCount,
  priceDriftCount,
  type FilterableListing,
  type ListingFilters,
} from "./inventoryFilters";
import { Button } from "./ui/button";
import { Menu, MenuGroup, MenuGroupLabel, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "./ui/menu";

export type TableFilters = Pick<ListingFilters, "favorites" | "labels" | "excludeLabels" | "inactive" | "priceDrift" | "missingSku">;

export const NO_TABLE_FILTERS: TableFilters = {
  favorites: false, labels: [], excludeLabels: [], inactive: false, priceDrift: false, missingSku: false,
};

export function tableFilterCount(filters: TableFilters): number {
  return Number(filters.favorites) + filters.labels.length + filters.excludeLabels.length
    + Number(filters.inactive) + Number(filters.priceDrift) + Number(filters.missingSku);
}

export function InventoryTableFilters({ listings, filters, labelColors, pricingRulesOn, warnMissingSku, onChange }: {
  /** The listings on the current status tab, for the counts beside each row. */
  listings: FilterableListing[];
  filters: TableFilters;
  labelColors?: Record<string, LabelColor>;
  pricingRulesOn: boolean;
  warnMissingSku: boolean;
  onChange: (next: TableFilters) => void;
}) {
  const chosen = tableFilterCount(filters);
  const labels = labelOptions(listings);
  const counts = labelCounts(listings);
  // The label helpers take whole sidebar filters; the table keeps only its part.
  const asFilters = { ...filters } as ListingFilters;
  return (
    <Menu>
      <MenuTrigger render={<Button variant="outline" size="sm" />} aria-label={chosen ? `Filters, ${chosen} on` : "Filters"}>
        <ListFilterIcon />
        Filters
        {chosen ? <span className="text-xs text-muted-foreground tabular-nums">{chosen}</span> : null}
      </MenuTrigger>
      <MenuPopup align="start" className="max-h-[min(70vh,32rem)] min-w-60" aria-label="Filter inventory">
        <FilterRow checked={filters.favorites} count={favoriteCount(listings)} label="Favorites" title="Listings you starred"
          glyph={<FavoriteStar on className="size-3.5 shrink-0 text-muted-foreground" />}
          onSelect={() => onChange({ ...filters, favorites: !filters.favorites })} />
        <MenuSeparator />
        <MenuGroup>
          <MenuGroupLabel>Warnings</MenuGroupLabel>
          <FilterRow checked={filters.inactive} count={inactiveCount(listings)} glyph={<Dot />} label="Inactive"
            title="Still on a marketplace but not for sale there"
            onSelect={() => onChange({ ...filters, inactive: !filters.inactive })} />
          {pricingRulesOn || filters.priceDrift ? (
            <FilterRow checked={filters.priceDrift} count={priceDriftCount(listings)} glyph={<Dot tone="warning" />} label="Price inconsistency"
              title="Marketplace prices that differ from your pricing rules"
              onSelect={() => onChange({ ...filters, priceDrift: !filters.priceDrift })} />
          ) : null}
          {warnMissingSku || filters.missingSku ? (
            <FilterRow checked={filters.missingSku} count={missingSkuCount(listings)} glyph={<Dot tone="warning" />} label="Missing SKU"
              onSelect={() => onChange({ ...filters, missingSku: !filters.missingSku })} />
          ) : null}
        </MenuGroup>
        {labels.length ? (
          <>
            <MenuSeparator />
            <MenuGroup>
              <MenuGroupLabel>Labels · include ✓ or exclude ✗</MenuGroupLabel>
              {labels.map((label) => {
                const color = labelColor(labelColors, label);
                return (
                  <LabelRow key={label} label={label} count={counts[label.toLowerCase()] || 0}
                    state={labelFilterState(asFilters, label)}
                    glyph={color ? <span aria-hidden="true" className={cn("mx-[3px] size-2 shrink-0 rounded-full", labelDotClass(color))} /> : <Dot />}
                    onSelect={() => {
                      const next = cycleLabelFilter(asFilters, label);
                      onChange({ ...filters, labels: next.labels, excludeLabels: next.excludeLabels });
                    }} />
                );
              })}
            </MenuGroup>
          </>
        ) : null}
        {chosen ? (
          <>
            <MenuSeparator />
            <MenuItem onClick={() => onChange(NO_TABLE_FILTERS)}><XIcon />Clear filters</MenuItem>
          </>
        ) : null}
      </MenuPopup>
    </Menu>
  );
}
