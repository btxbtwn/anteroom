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

/** Muted field caption above a control. */
export function FieldLabel({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      className={cn("mb-1.5 block font-medium text-muted-foreground text-xs", className)}
      {...props}
    />
  );
}

/** Section heading inside the inspector. */
export function SectionHeading({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      className={cn("font-medium text-foreground text-sm", className)}
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
      className={cn("flex min-w-0 flex-col gap-3 border-b py-4 first:pt-1 last:border-b-0", className)}
    >
      <div className="flex min-h-7 min-w-0 items-center gap-2">
        <SectionHeading id={titleId} className="shrink-0">{title}</SectionHeading>
        {meta ? <span className="min-w-0 truncate text-muted-foreground text-xs">{meta}</span> : null}
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
          placeholder="Search"
          showClear={Boolean(value) && !disabled}
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
            placeholder={value.length > 0 ? "" : (selectionOnly ? "Search choices" : "Search or add values")}
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
      {(help || maxValues != null) && <div className="mt-1.5 flex items-start gap-3 text-xs text-muted-foreground">
        {help && <p id={`${id}-help`} className="min-w-0 flex-1">{help}</p>}
        {maxValues != null && <p className={cn("ms-auto shrink-0 tabular-nums", overLimit && "text-warning-foreground")}
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
  /** What the closed category dropdown reads, such as "Women Jeans". */
  display: string;
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

type Drill = { department: string; group?: string };

function cascadeMatch(departments: readonly CascadeDepartment[], value: string): { category: CascadeCategory; department: CascadeDepartment } | null {
  for (const department of departments) {
    const categories = department.items.flatMap(item => item.type === "group" ? item.items : [item]);
    const category = categories.find(item => item.value === value || item.children.some(child => child.value === value));
    if (category) return { category, department };
  }
  return null;
}

/**
 * Two dropdowns. The first drills departments, then categories, then a group.
 * The second lists that category's subcategories and None.
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
  const [open, setOpen] = React.useState(false);
  const [drill, setDrill] = React.useState<Drill | null>(null);
  const root = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const level = open ? drill : null;
  const department = departments.find(entry => entry.id === level?.department);
  const options: { key: string; label: string; pick: () => void }[] = [];
  if (!department) {
    for (const entry of departments) options.push({
      key: entry.id, label: entry.label, pick: () => setDrill({ department: entry.id }),
    });
  } else {
    options.push({ key: "all", label: "All Categories", pick: () => setDrill(null) });
    const items = level?.group
      ? (department.items.find(item => item.type === "group" && item.label === level.group) as CascadeGroup | undefined)?.items || []
      : department.items;
    for (const item of items) {
      if (item.type === "group") options.push({ key: item.label, label: item.label, pick: () => setDrill({ department: department.id, group: item.label }) });
      else options.push({
        key: item.value, label: item.label, pick: () => {
          onChange(item.value);
          setOpen(false);
          setDrill(null);
        },
      });
    }
  }
  const subcategory = match && match.category.children.find(child => child.value === value && child.value !== match.category.value);
  return (
    <div ref={root} className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="relative min-w-0">
        <FieldLabel htmlFor={id}>Category</FieldLabel>
        <button type="button" id={id} data-listing-field={id} aria-haspopup="listbox" aria-expanded={open} disabled={disabled}
          className="flex h-8.5 w-full items-center truncate rounded-lg border border-input bg-background px-3 text-left text-base shadow-xs/5 sm:h-7.5 sm:text-sm disabled:opacity-64"
          onClick={() => { setDrill(null); setOpen(current => !current); }}>
          {match ? match.category.display : "Select Category"}
        </button>
        {open && <ul role="listbox" aria-label="Category" className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border bg-popover p-1 shadow-md">
          {options.map(option => <li key={option.key} role="option" aria-selected={option.label === match?.department.label}>
            <button type="button" className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent" onClick={option.pick}>{option.label}</button>
          </li>)}
        </ul>}
      </div>
      {match?.category.children.length ? <div className="min-w-0">
        <FieldLabel htmlFor={`${id}-subcategory`}>Subcategory</FieldLabel>
        <NativeSelect id={`${id}-subcategory`} aria-label="Subcategory" disabled={disabled}
          value={subcategory?.value || ""} onChange={event => onChange(event.target.value || match.category.value)}>
          <option value="">Select Subcategory (optional)</option>
          {match.category.children.map(child => <option key={`${child.label}-${child.value}`} value={child.value}>{child.label}</option>)}
        </NativeSelect>
      </div> : null}
    </div>
  );
}
