/**
 * Small form pieces the listing editor shares, built from T3 Code's control
 * styles (MIT, (c) 2026 T3 Tools Inc.): its segmented toggle for tab strips,
 * its input chrome for native selects, its muted field labels.
 */
import { ChevronDownIcon } from "lucide-react";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { cn } from "../lib/utils";
import { Combobox, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxPopup } from "./ui/combobox";
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
}) {
  const [query, setQuery] = React.useState(value);
  React.useEffect(() => setQuery(value), [value]);
  const custom = !selectionOnly && query.trim() && !options.includes(query.trim()) ? query.trim() : "";
  const matches = options.length > 200 ? options.filter((item) => item.toLowerCase().includes(query.toLowerCase())).slice(0, 100) : options;
  const items = custom ? [custom, ...matches] : matches;
  const unavailable = Boolean(selectionOnly && options.length && value && !options.includes(value));
  return (
    <div className="min-w-0">
      <Combobox<string>
        items={items}
        filter={onSearch ? null : undefined}
        value={value || null}
        inputValue={query}
        disabled={disabled}
        onValueChange={(next) => onChange(next ?? "")}
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
            {(item: string) => <ComboboxItem key={item} value={item}>{item === custom ? `Use “${item}”` : item}</ComboboxItem>}
          </ComboboxList>
        </ComboboxPopup>
      </Combobox>
      {value && !disabled && <button type="button" className="mt-1 text-xs text-muted-foreground underline" aria-label="Clear value"
        onMouseDown={(event) => { event.preventDefault(); onChange(""); }}>Clear</button>}
      {unavailable && <p id={`${id}-unavailable`} role="alert" className="mt-1.5 text-xs text-warning-foreground">
        {choicesVerified
          ? `Saved value “${value}” is unavailable in these choices. Choose another value or clear it.`
          : `Saved value “${value}” is outside these reference choices. Verify it on the marketplace before changing it.`}
      </p>}
    </div>
  );
}

/** Category names from eBay's own taxonomy search; selecting one stores its ID. */
export function EbayCategorySelect({ id, value, onChange }: {
  id: string; value: string; onChange: (id: string, path: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);
  const result = useQuery({ queryKey: ["ebay-category-search", search],
    queryFn: () => api.ebay.read("categories", { q: search }), enabled: search.length >= 3 && search !== value, retry: false });
  const choices = (result.data?.rows || []).map((row) => ({ id: row.id, path: [row.subtitle, row.title].filter(Boolean).join(" › ") }));
  return <div>
    <SearchableSelect id={id} fieldKey="direct_category_ids.ebay" value={value}
      options={[...new Set([value, ...choices.map((row) => row.path)].filter(Boolean))]} onSearch={setQuery}
      onChange={(label) => { const choice = choices.find((row) => row.path === label); if (choice) onChange(choice.id, choice.path); }} />
    <p role="status" className="mt-1 text-xs text-muted-foreground">{result.isError ? "eBay category search failed. Check its connection and try again."
      : result.isFetching ? "Searching eBay categories…" : search.length >= 3 && search !== value
        ? `${choices.length} eBay categories found.` : "Type at least 3 characters to search eBay categories."}</p>
  </div>;
}

/** A category's multiple values stay an array, including values awaiting correction. */
export function SearchableMultiSelect({
  id, fieldKey, value, options, onChange, disabled, selectionOnly = true, maxValues, maxLength, choicesVerified = true,
}: {
  id: string;
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
  return (
    <div ref={anchor} className="min-w-0">
      <Combobox<string, true>
        multiple items={items} value={value} disabled={disabled}
        onInputValueChange={setQuery}
        onValueChange={(next) => {
          if (maxValues != null && next.length > maxValues && next.length > value.length) return;
          onChange(next);
        }}
      >
        {value.length > 0 && <ul aria-label="Selected values" className="mb-2 flex flex-wrap gap-1.5">
          {value.map((item) => <li key={item} className="flex max-w-full items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1 text-xs">
            <span className="break-words">{item}</span>
            <button type="button" disabled={disabled} aria-label={`Remove ${item}`}
              className="shrink-0 rounded px-1 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              onClick={() => onChange(value.filter((entry) => entry !== item))}>×</button>
          </li>)}
        </ul>}
        <ComboboxInput id={id} data-listing-field={fieldKey} placeholder={selectionOnly ? "Search choices" : "Search or add values"}
          disabled={disabled} maxLength={maxLength} aria-invalid={invalid || undefined}
          aria-describedby={unavailable.length > 0 || overLimit ? `${id}-unavailable` : undefined} />
        <ComboboxPopup anchor={anchor}>
          <ComboboxEmpty>No matches.</ComboboxEmpty>
          <ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}
            disabled={maxValues != null && value.length >= maxValues && !value.includes(item)}>
            <span aria-hidden="true">{value.includes(item) ? "✓" : "○"}</span>
            {item === custom ? `Add “${item}”` : item}
          </ComboboxItem>}</ComboboxList>
        </ComboboxPopup>
      </Combobox>
      {value.length > 0 && !disabled && <button type="button" className="mt-1 text-xs text-muted-foreground underline" aria-label="Clear values"
        onMouseDown={(event) => { event.preventDefault(); onChange([]); }}>Clear</button>}
      {(unavailable.length > 0 || overLimit) && <p id={`${id}-unavailable`} role="alert" className="mt-1.5 text-xs text-warning-foreground">
        {unavailable.length > 0 && (choicesVerified
          ? `Unavailable saved values: ${unavailable.join(", ")}. Remove or replace them. `
          : `Saved values outside these reference choices: ${unavailable.join(", ")}. Verify them on the marketplace. `)}
        {overLimit && `Choose at most ${maxValues} values.`}
      </p>}
      {maxValues != null && <p className="mt-1 text-xs text-muted-foreground">{value.length} of {maxValues} selected</p>}
    </div>
  );
}
