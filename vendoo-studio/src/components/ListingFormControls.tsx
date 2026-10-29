/**
 * Small form pieces the listing editor shares, built from T3 Code's control
 * styles (MIT, (c) 2026 T3 Tools Inc.): its segmented toggle for tab strips,
 * its input chrome for native selects, its muted field labels.
 */
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, ChevronsUpDownIcon, XIcon } from "lucide-react";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { catalogLeafChoices, categoryChoiceAt, categoryMenu, categoryPathKey, categorySearch, categorySegments, type CategoryChoice, type CategoryItem } from "./categoryTree";
import { cn } from "../lib/utils";
import { Combobox, ComboboxCollection, ComboboxEmpty, ComboboxGroup, ComboboxGroupLabel, ComboboxInput, ComboboxItem, ComboboxList, ComboboxListVirtualized, ComboboxPopup, ComboboxSearchInput, ComboboxStatus, ComboboxTrigger } from "./ui/combobox";
import { Popover, PopoverPopup, PopoverTrigger } from "./ui/popover";
import { toggleVariants } from "./ui/toggle";

/** A tab strip in T3's segmented-control look; keeps tablist/tab roles. */
export function SegmentedTabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  className,
}: {
  tabs: readonly { id: T; label: React.ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("flex w-fit gap-0.5 rounded-lg bg-input/40 p-0.5", className)}
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          data-pressed={value === tab.id ? "" : undefined}
          className={cn(toggleVariants({ variant: "segmented", size: "segmented" }))}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/** Field caption above a control: dimmer than the value, but still readable. */
export function FieldLabel({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      className={cn("mb-1.5 block font-medium text-foreground/75 text-[13px] leading-4", className)}
      {...props}
    />
  );
}

/** Section heading inside the inspector. */
export function SectionHeading({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      className={cn("font-semibold text-[15px] text-foreground leading-5", className)}
      {...props}
    />
  );
}

/** One titled block of the inspector: heading row (title, meta, actions), then body. */
export function InspectorSection({
  title,
  titleId,
  meta,
  actions,
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"section">, "title"> & {
  title: React.ReactNode;
  titleId?: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section
      {...props}
      aria-labelledby={titleId}
      className={cn("flex min-w-0 flex-col gap-4 border-b pt-6 pb-6 first:pt-1 last:border-b-0", className)}
    >
      <div className="flex min-h-7 min-w-0 items-center gap-2">
        <SectionHeading id={titleId} className="shrink-0">{title}</SectionHeading>
        {meta ? <span className="min-w-0 truncate text-muted-foreground text-[13px]">{meta}</span> : null}
        {actions ? <div className="ms-auto flex shrink-0 items-center gap-1">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Native select with T3's input chrome: keeps the platform picker and keyboard. */
export function NativeSelect({
  className,
  size = "default",
  ...props
}: Omit<React.ComponentProps<"select">, "size"> & { size?: "default" | "sm" }) {
  return (
    <span
      data-slot="select-control"
      className={cn(
        "relative inline-flex w-full rounded-lg border border-input bg-background not-dark:bg-clip-padding text-base text-foreground shadow-xs/5 ring-ring/24 transition-shadow has-focus-visible:border-ring has-focus-visible:ring-[3px] has-focus-visible:ring-inset has-disabled:opacity-64 sm:text-sm dark:bg-input/32",
        className,
      )}
    >
      <select
        className={cn(
          "w-full min-w-0 cursor-pointer appearance-none truncate rounded-[inherit] bg-transparent pe-8 ps-[calc(--spacing(3)-1px)] outline-none",
          size === "sm" ? "h-7.5 sm:h-6.5" : "h-8.5 sm:h-7.5",
        )}
        {...props}
      />
      <ChevronDownIcon
        aria-hidden="true"
        className="pointer-events-none absolute end-2.5 top-1/2 size-3.5 -translate-y-1/2 text-icon-muted opacity-80"
      />
    </span>
  );
}

/** Keep an unavailable saved value visible without offering it as a valid choice. */
type ChoiceGroup = { key: string; value: string; items: string[] };

/** Consecutive choices under the same heading, in the marketplace's order. */
export function groupedChoices(options: string[], headings: Record<string, string>): ChoiceGroup[] {
  const groups: ChoiceGroup[] = [];
  for (const option of options) {
    const heading = headings[option] ?? "";
    const last = groups[groups.length - 1];
    if (last && last.value === heading) last.items.push(option);
    else groups.push({ key: `${groups.length}-${heading}`, value: heading, items: [option] });
  }
  return groups;
}

export function SearchableSelect({
  id,
  fieldKey,
  value,
  options,
  onChange,
  disabled,
  selectionOnly = true,
  maxLength,
  choicesVerified = true,
  onSearch,
  optionGroups,
  placeholder = "Search",
  clearable = true,
}: {
  id: string;
  fieldKey: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
  selectionOnly?: boolean;
  maxLength?: number;
  choicesVerified?: boolean;
  onSearch?: (query: string) => void;
  /** Choice label → the heading the marketplace lists it under, in option order. */
  optionGroups?: Record<string, string>;
  placeholder?: string;
  clearable?: boolean;
}) {
  const [query, setQuery] = React.useState(value);
  React.useEffect(() => setQuery(value), [value]);
  const custom = !selectionOnly && query.trim() && !options.includes(query.trim()) ? query.trim() : "";
  const matches = options.length > 200 ? options.filter((item) => item.toLowerCase().includes(query.toLowerCase())).slice(0, 100) : options;
  const items = custom ? [custom, ...matches] : matches;
  const groups = optionGroups && !custom ? groupedChoices(items, optionGroups) : null;
  const unavailable = Boolean(selectionOnly && options.length && value && !options.includes(value));
  return (
    <div className="min-w-0">
      <Combobox<string>
        items={(groups ?? items) as string[]}
        filter={onSearch ? null : undefined}
        value={value || null}
        inputValue={query}
        disabled={disabled}
        onValueChange={(next, details) => {
          // Replacing the search text is not a committed clear. Autosaving that
          // intermediate null remounts the category form and interrupts typing.
          if (next === null && details.reason === "input-clear") { details.cancel(); return; }
          onChange(next ?? "");
        }}
        onInputValueChange={(next) => { setQuery(next); onSearch?.(next); }}
      >
        <ComboboxInput
          id={id}
          data-listing-field={fieldKey}
          placeholder={placeholder}
          showClear={clearable && Boolean(value) && !disabled}
          disabled={disabled}
          maxLength={maxLength}
          aria-invalid={(unavailable && choicesVerified) || undefined}
          aria-describedby={unavailable ? `${id}-unavailable` : undefined}
        />
        <ComboboxPopup>
          <ComboboxEmpty>No matches.</ComboboxEmpty>
          <ComboboxList>
            {groups
              ? (group: ChoiceGroup) => (
                <ComboboxGroup key={group.key} items={group.items}>
                  {group.value && <ComboboxGroupLabel>{group.value}</ComboboxGroupLabel>}
                  <ComboboxCollection>
                    {(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}
                  </ComboboxCollection>
                </ComboboxGroup>
              )
              : (item: string) => <ComboboxItem key={item} value={item}>{item === custom ? `Use “${item}”` : item}</ComboboxItem>}
          </ComboboxList>
        </ComboboxPopup>
      </Combobox>
      {unavailable && <p id={`${id}-unavailable`} role="alert" className="mt-1.5 text-xs text-warning-foreground">
        {choicesVerified
          ? `Saved value “${value}” is unavailable in these choices. Choose another value or clear it.`
          : `Saved value “${value}” is outside these reference choices. Verify it on the marketplace before changing it.`}
      </p>}
    </div>
  );
}

/**
 * A category button that drills one level at a time, the way Poshmark's
 * category picker works. The chosen path reads in full with an arrow between
 * levels; the popover shows where the path sits, and searches every category
 * when the seller does not want to drill.
 */
export function CategoryDrilldown({
  id, fieldKey, value, choices, disabled, placeholder = "Choose a category", onChange,
}: {
  id: string;
  fieldKey?: string;
  value: readonly string[];
  choices: readonly CategoryChoice[];
  disabled?: boolean;
  placeholder?: string;
  onChange: (choice: CategoryChoice) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [drill, setDrill] = React.useState<string[]>([]);
  const [query, setQuery] = React.useState("");
  const searchRef = React.useRef<HTMLInputElement>(null);
  // On a desktop the seller can type straight away; on touch, leave the tree
  // in view instead of covering it with the keyboard.
  React.useEffect(() => {
    if (!open || window.matchMedia("(pointer: coarse)").matches) return;
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open, drill]);
  const selectedKey = categoryPathKey(value);
  const searching = query.trim().length > 0;
  const menu = React.useMemo(() => categoryMenu(choices, drill), [choices, drill]);
  const results = React.useMemo(() => (searching ? categorySearch(choices, query) : []), [choices, query, searching]);
  const items = React.useMemo(() => (searching ? results : menu?.items ?? []), [menu, results, searching]);
  const itemsByKey = React.useMemo(() => new Map(items.map((item) => [categoryPathKey(item.path), item])), [items]);
  const itemKeys = React.useMemo(() => [...itemsByKey.keys()], [itemsByKey]);
  const crumbs = ["All categories", ...drill];
  const choose = (item: CategoryItem) => {
    const choice = categoryChoiceAt(choices, item.path);
    if (choice) {
      onChange(choice);
      setOpen(false);
      return;
    }
    setDrill(item.path);
    setQuery("");
  };
  return (
    <Popover open={open} onOpenChange={(next) => {
      setOpen(next);
      setQuery("");
      // Open where the saved category lives so its siblings are in view.
      // A saved path this catalog does not know, such as the shared category
      // path copied onto a marketplace, starts at the top instead of an empty
      // level the seller cannot back out of.
      if (next) {
        const prefix = value.length > 1 ? value.slice(0, -1) : [];
        setDrill(value.length && categoryMenu(choices, prefix) ? prefix : []);
      }
    }}>
      <PopoverTrigger id={id} data-listing-field={fieldKey} disabled={disabled}
        aria-label={value.length ? `Category: ${value.join(" → ")}` : placeholder}
        title={value.length ? value.join(" → ") : undefined}
        className="flex min-h-8.5 w-full items-start justify-between gap-2 rounded-lg border border-input bg-background px-3 py-1.5 text-left text-base shadow-xs/5 disabled:opacity-64 sm:min-h-7.5 sm:text-sm">
        <span className={cn("min-w-0 flex-1 whitespace-normal break-words leading-snug", !value.length && "text-muted-foreground")}>
          {value.length
            ? value.map((part, index) => (
              <React.Fragment key={`${index}-${part}`}>
                {index > 0 && <span aria-hidden="true" className="mx-1 text-muted-foreground/70">→</span>}
                <span>{part}</span>
              </React.Fragment>
            ))
            : placeholder}
        </span>
        <ChevronDownIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-icon-muted opacity-80" />
      </PopoverTrigger>
      <PopoverPopup align="start" padding="none"
        className="w-[max(var(--anchor-width),24rem)] max-w-(--available-width) p-0">
        <Combobox<string>
          inline
          open
          items={itemKeys}
          filteredItems={itemKeys}
          filter={null}
          autoHighlight
          // Keep the search text as typed; a picked row must not become the query.
          autoComplete="none"
          onValueChange={(key) => {
            if (typeof key !== "string") return;
            const item = itemsByKey.get(key);
            if (item) choose(item);
          }}>
          <div className="flex min-h-0 flex-col" onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }
          }}>
            <nav aria-label="Category path" className="flex flex-wrap items-center gap-x-1 gap-y-0.5 px-3 pt-2.5 pb-1 text-xs">
              {crumbs.map((part, index) => {
                const current = index === crumbs.length - 1;
                return (
                  <React.Fragment key={`${index}-${part}`}>
                    {index > 0 && <ChevronRightIcon aria-hidden="true" className="size-3 shrink-0 text-muted-foreground/60" />}
                    {current
                      ? <span aria-current="page" className="px-1 py-0.5 font-medium text-foreground">{part}</span>
                      : <button type="button" className="rounded px-1 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                          onClick={() => { setDrill(index === 0 ? [] : drill.slice(0, index)); setQuery(""); }}>
                          {part}
                        </button>}
                  </React.Fragment>
                );
              })}
            </nav>
            <ComboboxSearchInput ref={searchRef} value={query} aria-label="Search all categories"
              placeholder="Search all categories" onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft" && !query && drill.length) {
                  event.preventDefault();
                  setDrill(drill.slice(0, -1));
                }
              }} />
            <ComboboxListVirtualized className="size-auto max-h-64 min-h-0 overflow-y-auto overscroll-contain not-empty:p-1">
              {itemKeys.map((key) => {
                const item = itemsByKey.get(key)!;
                return (
                  <ComboboxItem key={key} value={key} className="items-start gap-2">
                    {searching
                      ? <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1 gap-y-0.5 text-left">
                          {item.path.map((part, index) => (
                            <React.Fragment key={`${index}-${part}`}>
                              {index > 0 && <span aria-hidden="true" className="text-muted-foreground/50">›</span>}
                              <span className={index === item.path.length - 1 ? "text-foreground" : "text-muted-foreground"}>{part}</span>
                            </React.Fragment>
                          ))}
                        </span>
                      : <span className="min-w-0 flex-1 text-left">{item.label}</span>}
                    {key === selectedKey && <>
                      <CheckIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                      <span className="sr-only">Selected</span>
                    </>}
                    {!item.terminal && <ChevronRightIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
                  </ComboboxItem>
                );
              })}
            </ComboboxListVirtualized>
            <ComboboxEmpty>
              {searching ? `No categories match “${query.trim()}”.` : "No categories here. Search or go back a level."}
            </ComboboxEmpty>
            <ComboboxStatus className="sr-only">
              {searching
                ? `${itemKeys.length} ${itemKeys.length === 1 ? "category" : "categories"} match ${query.trim()}.`
                : itemKeys.length ? `${crumbs[crumbs.length - 1]} has ${itemKeys.length} ${itemKeys.length === 1 ? "category" : "categories"}.` : "No categories here."}
            </ComboboxStatus>
          </div>
        </Combobox>
      </PopoverPopup>
    </Popover>
  );
}

/**
 * A category path picker. The seller drills one level per step, as Poshmark's
 * selector does, and a group does not save until a real category is chosen.
 */
export function CategoryTreeSelect({
  id, fieldKey, value, choices, disabled, onChange,
}: {
  id: string;
  fieldKey: string;
  value: string;
  choices: readonly CategoryChoice[];
  disabled?: boolean;
  onChange: (choice: CategoryChoice) => void;
}) {
  const selected = categorySegments(value);
  const savedKnown = Boolean(value) && Boolean(categoryChoiceAt(choices, selected));
  if (!choices.length) {
    return <p role="status" className="text-xs text-muted-foreground">No categories are available yet. Reopen the listing to try again.</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <CategoryDrilldown id={id} fieldKey={fieldKey} value={selected} choices={choices} disabled={disabled} onChange={onChange} />
      {Boolean(value) && !savedKnown && (
        <p role="alert" className="text-xs text-warning-foreground">Saved category “{value}” is not in this list. Choose one to replace it.</p>
      )}
    </div>
  );
}

/** eBay categories from the synced tree. Search stays available until that tree is complete. */
export function EbayCategorySelect({ id, value, onChange }: {
  id: string; value: string; onChange: (id: string, path: string) => void;
}) {
  const tree = useQuery({
    queryKey: ["catalog-categories", "ebay"],
    queryFn: () => api.catalog.categories("ebay"),
    staleTime: Infinity,
    retry: false,
  });
  const leaves = React.useMemo(() => catalogLeafChoices(tree.data), [tree.data]);
  const treeReady = !tree.isPending;
  const searchFallback = treeReady && (leaves.length === 0 || tree.data?.complete === false);
  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);
  const result = useQuery({ queryKey: ["ebay-category-search", search],
    queryFn: () => api.ebay.read("categories", { q: search }),
    enabled: searchFallback && search.length >= 3 && search !== value, retry: false });
  const choices = (result.data?.rows || []).map((row) => ({ id: row.id, path: [row.subtitle, row.title].filter(Boolean).join(" › ") }));
  if (!treeReady) return <p role="status" className="text-xs text-muted-foreground">Loading categories…</p>;
  const searchPanel = (
    <>
      {leaves.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Search if the category is not in the list yet.</p>}
      <SearchableSelect id={leaves.length ? `${id}-search` : id} fieldKey="direct_category_ids.ebay" value={value}
        options={[...new Set([value, ...choices.map((row) => row.path)].filter(Boolean))]} onSearch={setQuery}
        onChange={(label) => { const choice = choices.find((row) => row.path === label); if (choice) onChange(choice.id, choice.path); }} />
      <p role="status" className="mt-1 text-xs text-muted-foreground">{result.isError ? "eBay category search failed. Check its connection and try again."
        : result.isFetching ? "Searching eBay categories…" : search.length >= 3 && search !== value
          ? `${choices.length} eBay categories found.` : "Type at least 3 characters to search eBay categories."}</p>
    </>
  );
  return <div>
    {leaves.length > 0 && <CategoryTreeSelect key={value} id={id} fieldKey="direct_category_ids.ebay" value={value} choices={leaves}
      onChange={(choice) => onChange(choice.id, choice.label.split(" > ").join(" › "))} />}
    {searchFallback ? searchPanel : null}
  </div>;
}

/** A final category from one of Anteroom's synced category trees (the shared tree or Depop's). */
export function TreeCategorySelect({ id, marketplace, name, value, onChange, disabled }: {
  id: string; marketplace: "general" | "depop"; name: string; value: string; onChange: (id: string, path: string) => void; disabled?: boolean;
}) {
  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);
  const result = useQuery({ queryKey: ["catalog-category-search", marketplace, search],
    queryFn: () => api.catalog.searchCategories(search, marketplace), enabled: search.length >= 3 && search !== value, retry: false });
  const choices = (result.data?.results || []).filter((row) => row.kind === "category" && row.marketplace === marketplace);
  return <div>
    <SearchableSelect id={id} fieldKey={`category.${marketplace}`} value={value} disabled={disabled}
      options={[...new Set([value, ...choices.map((row) => row.path)].filter(Boolean))]} onSearch={setQuery}
      onChange={(label) => { const choice = choices.find((row) => row.path === label); if (choice) onChange(choice.id, choice.path); }} />
    <p role="status" className="mt-1 text-xs text-muted-foreground">{result.isError ? `${name} category search failed. Sync categories in Settings and try again.`
      : result.isFetching ? `Searching ${name} categories…` : search.length >= 3 && search !== value
        ? `${choices.length} ${name} categories found.` : `Type at least 3 characters to search ${name} categories.`}</p>
  </div>;
}

/** A category's multiple values stay an array, including values awaiting correction. */
export function SearchableMultiSelect({
  id, fieldKey, value, options, onChange, disabled, selectionOnly = true, maxValues, maxLength, choicesVerified = true, help,
}: {
  id: string;
  /** Hint shown under the field, on the same line as the count. */
  help?: string;
  fieldKey: string;
  value: string[];
  options: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  selectionOnly?: boolean;
  maxValues?: number;
  maxLength?: number;
  choicesVerified?: boolean;
}) {
  const anchor = React.useRef<HTMLDivElement>(null);
  const [query, setQuery] = React.useState("");
  const custom = !selectionOnly && query.trim() && !options.includes(query.trim()) ? query.trim() : "";
  const items = custom ? [custom, ...options] : options;
  const unavailable = selectionOnly && options.length ? value.filter((item) => !options.includes(item)) : [];
  const overLimit = maxValues != null && value.length > maxValues;
  const invalid = (choicesVerified && unavailable.length > 0) || overLimit;
  const clearable = value.length > 0 && !disabled;
  const describedBy = [help ? `${id}-help` : "", unavailable.length > 0 || overLimit ? `${id}-unavailable` : ""]
    .filter(Boolean).join(" ") || undefined;
  return (
    <div className="min-w-0">
      <Combobox<string, true>
        multiple items={items} value={value} disabled={disabled}
        onInputValueChange={setQuery}
        onValueChange={(next) => {
          if (maxValues != null && next.length > maxValues && next.length > value.length) return;
          onChange(next);
        }}
      >
        <div
          ref={anchor}
          data-slot="multi-select"
          className={cn(
            "relative flex min-h-8.5 w-full cursor-text flex-wrap items-center gap-1 rounded-lg border border-input bg-background py-1 ps-1.5 text-base text-foreground shadow-xs/5 ring-ring/24 transition-shadow not-dark:bg-clip-padding",
            "has-focus-visible:border-ring has-focus-visible:ring-[3px] has-focus-visible:ring-inset",
            "has-aria-invalid:border-destructive/36 has-focus-visible:has-aria-invalid:border-destructive/64 has-focus-visible:has-aria-invalid:ring-destructive/16",
            "has-disabled:opacity-64 sm:min-h-7.5 sm:text-sm dark:bg-input/32 dark:has-aria-invalid:ring-destructive/24",
            clearable ? "pe-14" : "pe-8",
          )}
          onMouseDown={(event) => {
            if (disabled || (event.target as HTMLElement).closest("button, input")) return;
            event.preventDefault();
            anchor.current?.querySelector("input")?.focus();
          }}
        >
          {value.length > 0 && <ul aria-label="Selected values" className="contents">
            {value.map((item) => <li key={item} className="flex max-w-full items-center gap-0.5 rounded-md border border-border bg-secondary px-1.5 py-0.5 text-xs leading-4">
              <span className="min-w-0 break-words">{item}</span>
              <button type="button" disabled={disabled} aria-label={`Remove ${item}`}
                className="shrink-0 rounded px-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                onMouseDown={(event) => event.stopPropagation()}
                onClick={() => onChange(value.filter((entry) => entry !== item))}>×</button>
            </li>)}
          </ul>}
          <ComboboxInput
            embedded
            showTrigger={false}
            rootClassName={query.trim() ? "min-w-16" : "min-w-0"}
            id={id}
            data-listing-field={fieldKey}
            placeholder={value.length > 0 ? "" : maxValues != null
              ? (maxValues === 1 ? "Choose one" : `Choose up to ${maxValues}`)
              : (selectionOnly ? "Search choices" : "Search or add values")}
            disabled={disabled}
            maxLength={maxLength}
            aria-invalid={invalid || undefined}
            aria-describedby={describedBy}
          />
          {clearable && <button type="button" aria-label="Clear values" title="Clear"
            className="absolute end-7.5 top-1/2 inline-flex size-6 -translate-y-1/2 cursor-pointer items-center justify-center rounded-md text-icon-muted opacity-80 outline-none hover:bg-accent hover:opacity-100 focus-visible:outline-2 focus-visible:outline-ring [&_svg]:size-3.5"
            onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); onChange([]); }}
            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onChange([]); } }}>
            <XIcon />
          </button>}
          <ComboboxTrigger
            className="absolute end-0.5 top-1/2 inline-flex size-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded-md text-icon-muted opacity-80 outline-none hover:opacity-100 focus-visible:outline-2 focus-visible:outline-ring [&_svg]:size-4"
          >
            <ChevronsUpDownIcon />
          </ComboboxTrigger>
        </div>
        <ComboboxPopup anchor={anchor}>
          <ComboboxEmpty>No matches.</ComboboxEmpty>
          <ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}
            disabled={maxValues != null && value.length >= maxValues && !value.includes(item)}>
            <span aria-hidden="true">{value.includes(item) ? "✓" : "○"}</span>
            {item === custom ? `Add “${item}”` : item}
          </ComboboxItem>}</ComboboxList>
        </ComboboxPopup>
      </Combobox>
      {/* The empty picker's placeholder names the limit; the count takes over once something is chosen. */}
      {(help || (maxValues != null && value.length > 0)) && <div className="mt-1.5 flex items-start gap-3 text-xs text-muted-foreground">
        {help && <p id={`${id}-help`} className="min-w-0 flex-1">{help}</p>}
        {maxValues != null && value.length > 0 && <p className={cn("ms-auto shrink-0 tabular-nums", overLimit && "text-warning-foreground")}
          aria-label={`${value.length} of ${maxValues} selected`}>{value.length}/{maxValues}</p>}
      </div>}
      {(unavailable.length > 0 || overLimit) && <p id={`${id}-unavailable`} role="alert" className="mt-1.5 text-xs text-warning-foreground">
        {unavailable.length > 0 && (choicesVerified
          ? `Unavailable saved values: ${unavailable.join(", ")}. Remove or replace them. `
          : `Saved values outside these reference choices: ${unavailable.join(", ")}. Verify them on the marketplace. `)}
        {overLimit && `Choose at most ${maxValues} values.`}
      </p>}
    </div>
  );
}

/** One category in a department, or a group of categories such as Global & Traditional Wear. */
export interface CascadeCategory {
  type: "category";
  label: string;
  value: string;
  children: { label: string; value: string }[];
}

export interface CascadeGroup {
  type: "group";
  label: string;
  items: CascadeCategory[];
}

export interface CascadeDepartment {
  id: string;
  label: string;
  items: (CascadeCategory | CascadeGroup)[];
}

type CascadeMatch = { category: CascadeCategory; department: CascadeDepartment; group?: CascadeGroup };

function cascadeMatch(departments: readonly CascadeDepartment[], value: string): CascadeMatch | null {
  for (const department of departments) {
    for (const item of department.items) {
      const categories = item.type === "group" ? item.items : [item];
      const category = categories.find((row) => row.value === value || row.children.some((child) => child.value === value));
      if (category) return { category, department, ...(item.type === "group" ? { group: item } : {}) };
    }
  }
  return null;
}

function cascadeChoices(departments: readonly CascadeDepartment[]): CategoryChoice[] {
  return departments.flatMap((department) => department.items.flatMap((item) => {
    if (item.type === "group") {
      return item.items.map((category) => ({
        label: [department.label, item.label, category.label].join(" > "), id: category.value,
      }));
    }
    return [{ label: [department.label, item.label].join(" > "), id: item.value }];
  }));
}

/**
 * Poshmark's category picker: the category drills departments, groups and
 * categories, the selected path reads with arrows, and the subcategory sits
 * beside it when the category has narrower choices.
 */
export function DependentSelectPair({
  id, value, departments, disabled, onChange,
}: {
  id: string;
  value: string;
  departments: readonly CascadeDepartment[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const match = cascadeMatch(departments, value);
  const choices = React.useMemo(() => cascadeChoices(departments), [departments]);
  const path = match ? [match.department.label, ...(match.group ? [match.group.label] : []), match.category.label] : [];
  const subcategory = match && match.category.children.find((child) => child.value === value && child.value !== match.category.value);
  return (
    <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-end">
      <div className="min-w-0 sm:flex-1">
        <FieldLabel htmlFor={id}>Category</FieldLabel>
        <CategoryDrilldown id={id} value={path} choices={choices} disabled={disabled} placeholder="Select Category"
          onChange={(choice) => onChange(choice.id)} />
      </div>
      {match?.category.children.length ? <>
        <span aria-hidden="true" className="hidden shrink-0 pb-2 text-muted-foreground sm:inline">→</span>
        <div className="min-w-0 sm:flex-1">
          <FieldLabel htmlFor={`${id}-subcategory`}>Subcategory</FieldLabel>
          <NativeSelect id={`${id}-subcategory`} aria-label="Subcategory" disabled={disabled}
            value={subcategory?.value || ""} onChange={(event) => onChange(event.target.value || match.category.value)}>
            <option value="">Select Subcategory (optional)</option>
            {match.category.children.map((child) => <option key={`${child.label}-${child.value}`} value={child.value}>{child.label}</option>)}
          </NativeSelect>
        </div>
      </> : null}
    </div>
  );
}
