// Settings page, section, group and row, after T3 Code's settingsLayout.tsx and
// SettingsGroup.tsx (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc. T3's environment/project scoping is left out: Studio
// has one machine and one settings scope.
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";

export function SettingsPageContainer({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="topbar-scroll-fade min-h-0 flex-1 overflow-y-auto" data-settings-page-scroll>
      <div className={cn("mx-auto flex w-full max-w-4xl flex-col gap-8 px-5 pt-6 pb-12 sm:px-6", className)}>
        {children}
      </div>
    </div>
  );
}

export function SettingsGroup({
  variant = "grouped",
  divided = true,
  className,
  ...props
}: ComponentPropsWithoutRef<"div"> & { variant?: "grouped" | "plain"; divided?: boolean }) {
  return (
    <div
      {...props}
      className={cn(
        "relative overflow-visible text-foreground",
        variant === "grouped" ? "rounded-xl border border-border/60 bg-card/40 shadow-xs/5" : "space-y-1",
        variant === "grouped" &&
          divided &&
          "[&>*+*]:border-t [&>*+*]:border-border/50 [&>[data-slot=settings-row]]:rounded-none",
        className,
      )}
    />
  );
}

export function SettingsSection({
  title,
  hideTitle = false,
  icon,
  titleAction,
  headerAction,
  variant = "grouped",
  children,
  className,
  ...sectionProps
}: Omit<ComponentPropsWithoutRef<"section">, "title"> & {
  title: ReactNode;
  hideTitle?: boolean;
  icon?: ReactNode;
  titleAction?: ReactNode;
  headerAction?: ReactNode;
  variant?: "grouped" | "plain";
  children: ReactNode;
}) {
  return (
    <section
      {...sectionProps}
      tabIndex={sectionProps.id ? -1 : sectionProps.tabIndex}
      className={cn(!hideTitle && "space-y-2.5", "outline-none", className)}
    >
      {hideTitle ? (
        <h2 className="sr-only">{title}</h2>
      ) : (
        <div className="flex min-h-7 items-start justify-between gap-4 px-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-1">
            <h2 className="flex min-h-7 items-center gap-2 text-sm font-normal text-foreground/70">
              {icon}
              {title}
            </h2>
            {titleAction}
          </div>
          <div className="flex min-h-7 min-w-7 items-center justify-end">{headerAction}</div>
        </div>
      )}
      <SettingsGroup variant={variant}>{children}</SettingsGroup>
    </section>
  );
}

export function SettingsRow({
  title,
  description,
  status,
  control,
  children,
  className,
  ...rowProps
}: Omit<ComponentPropsWithoutRef<"div">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  status?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div
      {...rowProps}
      tabIndex={rowProps.id ? -1 : rowProps.tabIndex}
      data-slot="settings-row"
      className={cn(
        "@container/settings-row rounded-xl px-3 outline-none sm:px-4 aria-disabled:opacity-64",
        children ? "pt-3 pb-3" : "py-3",
        className,
      )}
    >
      <div className="flex flex-col gap-3 @min-[32rem]/settings-row:grid @min-[32rem]/settings-row:grid-cols-[minmax(0,1fr)_minmax(10rem,auto)] @min-[32rem]/settings-row:items-center @min-[32rem]/settings-row:gap-8">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex min-h-5 items-center gap-1.5">
            <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">{title}</h3>
          </div>
          {description ? (
            <p className="max-w-xl text-xs leading-normal text-muted-foreground/80">{description}</p>
          ) : null}
          {status ? <div className="pt-0.5 text-xs text-muted-foreground">{status}</div> : null}
        </div>
        {control ? (
          <div className="flex w-full min-w-0 shrink-0 items-center gap-2 @min-[32rem]/settings-row:w-auto @min-[32rem]/settings-row:justify-end">
            {control}
          </div>
        ) : null}
      </div>
      {children ? <div className="mt-3 min-w-0 space-y-2">{children}</div> : null}
    </div>
  );
}

/** Secondary copy under a row: help text, progress or a load error. */
export function SettingsNote({
  tone = "muted",
  className,
  ...props
}: ComponentPropsWithoutRef<"p"> & { tone?: "muted" | "error" | "success" | "warning" }) {
  return (
    <p
      {...props}
      role={tone === "error" ? "alert" : props.role}
      className={cn(
        "max-w-xl text-xs leading-normal [overflow-wrap:anywhere]",
        tone === "muted" && "text-muted-foreground/80",
        tone === "error" && "text-destructive-foreground",
        tone === "success" && "text-success-foreground",
        tone === "warning" && "text-warning-foreground",
        className,
      )}
    />
  );
}

export function SettingsCode({ className, ...props }: ComponentPropsWithoutRef<"code">) {
  return (
    <code
      {...props}
      className={cn("rounded-sm bg-muted px-1 py-px font-mono text-2xs text-foreground [overflow-wrap:anywhere]", className)}
    />
  );
}

export type SettingsSelectOption<T extends string> = { value: T; label: ReactNode };

/** T3's settings picker: a small Select with its popup aligned to the trigger's end. */
export function SettingsSelect<T extends string>({
  value,
  options,
  onChange,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  value: T;
  options: readonly SettingsSelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
  "aria-label": string;
}) {
  const selected = options.find((option) => option.value === value);
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T)} disabled={disabled}>
      <SelectTrigger size="sm" className={cn("w-full sm:w-48", className)} aria-label={ariaLabel}>
        <SelectValue>
          <span className="flex min-w-0 items-center gap-2 truncate">{selected?.label ?? value}</span>
        </SelectValue>
      </SelectTrigger>
      <SelectPopup align="end" alignItemWithTrigger={false}>
        {options.map((option) => (
          <SelectItem hideIndicator key={option.value} value={option.value}>
            <span className="flex min-w-0 items-center gap-2">{option.label}</span>
          </SelectItem>
        ))}
      </SelectPopup>
    </Select>
  );
}
