/**
 * Small form pieces the listing editor shares, built from T3 Code's control
 * styles (MIT, (c) 2026 T3 Tools Inc.): its segmented toggle for tab strips,
 * its input chrome for native selects, its muted field labels.
 */
import { ChevronDownIcon, ChevronsUpDownIcon, XIcon } from "lucide-react";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { catalogLeafChoices, categoryChoiceAt, categoryPathKey, categorySegments, categorySteps, type CategoryChoice } from "./categoryTree";
import { cn } from "../lib/utils";
import { Combobox, ComboboxCollection, ComboboxEmpty, ComboboxGroup, ComboboxGroupLabel, ComboboxInput, ComboboxItem, ComboboxList, ComboboxPopup, ComboboxTrigger } from "./ui/combobox";
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
 * Grouped category dropdowns. The seller keeps picking until the row is the
 * last one in its branch. A group does not save, so the form stays put.
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
  // A group is not saved. This remembers the open branch; the parent remounts the control when the saved category changes.
  const [path, setPath] = React.useState<string[] | null>(null);
  const selected = path ?? categorySegments(value);
  const selectedKey = categoryPathKey(selected);
  const steps = React.useMemo(
    () => categorySteps(choices, selectedKey ? categorySegments(selectedKey) : []),
    [choices, selectedKey],
  );
  const atLeaf = categoryChoiceAt(choices, selected);
  const savedKnown = Boolean(value) && Boolean(categoryChoiceAt(choices, categorySegments(value)));
  const hasDeeper = steps.some((step) => step.menu.items.some((item) => !item.terminal));
  const choose = (next: string, index: number) => {
    if (!next) {
      if (index === 0) return;
      const previous = steps[index - 1]?.value;
      setPath(previous ? categorySegments(previous) : []);
      return;
    }
    const nextPath = categorySegments(next);
    setPath(nextPath);
    const choice = categoryChoiceAt(choices, nextPath);
    if (choice) onChange(choice);
  };
  if (!steps.length) {
    return <p role="status" className="text-xs text-muted-foreground">No categories are available yet. Reopen the listing to try again.</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {steps.map((step, index) => (
        <NativeSelect
          key={`level-${index}`}
          id={index === 0 ? id : `${id}-level-${index}`}
          data-listing-field={index === 0 ? fieldKey : undefined}
          aria-label={index === 0 ? undefined : "Next category"}
          disabled={disabled}
          value={step.value}
          onChange={(event) => choose(event.target.value, index)}
        >
          <option value="">{index === 0 ? "Choose a category" : "Choose a narrower category"}</option>
          {step.menu.items.map((item) => {
            const key = categoryPathKey(item.path);
            return <option key={key} value={key}>{item.label}</option>;
          })}
        </NativeSelect>
      ))}
      {!atLeaf && hasDeeper && <p className="text-xs text-muted-foreground">Choose a category group, then continue until the last one.</p>}
      {Boolean(value) && !savedKnown && path == null && (
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
