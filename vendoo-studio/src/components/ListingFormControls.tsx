/**
 * Small form pieces the listing editor shares, built from T3 Code's control
 * styles (MIT, (c) 2026 T3 Tools Inc.): its segmented toggle for tab strips,
 * its input chrome for native selects, its muted field labels.
 */
import { ChevronDownIcon } from "lucide-react";
import type * as React from "react";
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

/** A dropdown you can type into. A value the list does not contain stays visible. */
export function SearchableSelect({
  id,
  fieldKey,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string;
  fieldKey: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const items = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <Combobox<string>
      items={items}
      value={value || null}
      disabled={disabled}
      onValueChange={(next) => onChange(next ?? "")}
    >
      <ComboboxInput
        id={id}
        data-listing-field={fieldKey}
        placeholder="Search"
        showClear={Boolean(value) && !disabled}
        disabled={disabled}
      />
      <ComboboxPopup>
        <ComboboxEmpty>No matches.</ComboboxEmpty>
        <ComboboxList>
          {(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}
        </ComboboxList>
      </ComboboxPopup>
    </Combobox>
  );
}
