/*
 * Page layout for Studio's full-width pages (Marketplaces, Analytics,
 * Sourcing). The container, section header and grouped panel follow T3 Code's
 * WorkspacePageContainer, SettingsSection and SettingsGroup
 * (https://github.com/pingdotgg/t3code, MIT License, (c) 2026 T3 Tools Inc.).
 */
import type { ComponentProps, ReactNode } from "react";
import { CircleAlertIcon } from "lucide-react";

import { cn } from "../lib/utils";
import { Alert, AlertDescription } from "./ui/alert";
import { Skeleton } from "./ui/skeleton";

const WIDTH_CLASS = {
  readable: "max-w-4xl",
  wide: "max-w-5xl",
  expanded: "max-w-6xl",
} as const;

export function WorkspacePage({
  width = "wide",
  className,
  children,
}: {
  width?: keyof typeof WIDTH_CLASS;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="@container/page min-h-0 flex-1 overflow-y-auto bg-background text-foreground">
      <div
        className={cn(
          "mx-auto flex w-full flex-col gap-8 px-5 pt-6 pb-12 sm:px-6",
          WIDTH_CLASS[width],
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3">
      <div className="flex min-h-8 flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {description ? <p className="max-w-2xl text-sm text-muted-foreground">{description}</p> : null}
      {children}
    </header>
  );
}

export function PageSection({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: Omit<ComponentProps<"section">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={cn("flex min-w-0 flex-col gap-3", className)} {...props}>
      <div className="flex min-h-7 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 className="text-sm font-medium text-foreground">{title}</h2>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {description ? <PageNote>{description}</PageNote> : null}
      {children}
    </section>
  );
}

export function PageNote({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      className={cn("max-w-3xl text-xs leading-relaxed text-muted-foreground", className)}
      {...props}
    />
  );
}

/** A grouped surface with hairline dividers between its rows (T3's SettingsGroup). */
export function Panel({
  divided = true,
  className,
  ...props
}: ComponentProps<"div"> & { divided?: boolean }) {
  return (
    <div
      className={cn(
        "relative min-w-0 overflow-hidden rounded-xl border border-border/60 bg-card/40 text-foreground shadow-xs/5",
        divided && "[&>*+*]:border-t [&>*+*]:border-border/50",
        className,
      )}
      {...props}
    />
  );
}

export const rowButtonClassName =
  "flex w-full min-w-0 cursor-pointer items-center gap-4 px-4 py-2.5 text-left text-sm outline-none transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring";

/** One listing-like row: a title with a muted line under it, and a figure on the right. */
export function ListRow({
  title,
  meta,
  value,
  valueHint,
  onClick,
}: {
  title: ReactNode;
  meta?: ReactNode;
  value?: ReactNode;
  valueHint?: ReactNode;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-foreground">{title}</span>
        {meta ? <span className="truncate text-xs text-muted-foreground">{meta}</span> : null}
      </span>
      {value != null || valueHint ? (
        <span className="flex shrink-0 flex-col items-end gap-0.5 text-right">
          {value != null ? <span className="text-foreground tabular-nums">{value}</span> : null}
          {valueHint ? <span className="text-xs text-muted-foreground">{valueHint}</span> : null}
        </span>
      ) : null}
    </>
  );
  return (
    <li>
      {onClick ? (
        <button type="button" className={rowButtonClassName} onClick={onClick}>
          {content}
        </button>
      ) : (
        <div className="flex min-w-0 items-center gap-4 px-4 py-2.5 text-sm">{content}</div>
      )}
    </li>
  );
}

export function PageError({ message }: { message: string }) {
  return (
    <Alert variant="error">
      <CircleAlertIcon />
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

/** Placeholder for a page whose first load is still in flight. */
export function PageSkeleton({ label = "Loading…", cards = 4 }: { label?: string; cards?: number }) {
  return (
    <div className="flex flex-col gap-6" role="status" aria-label={label} aria-busy="true">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3.5 w-72 max-w-full" />
      </div>
      <div className="grid grid-cols-2 gap-3 @2xl/page:grid-cols-4">
        {Array.from({ length: cards }, (_, index) => (
          <Skeleton key={index} shape="card" className="h-24" />
        ))}
      </div>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-32" />
        <Skeleton shape="card" className="h-40" />
      </div>
    </div>
  );
}

/** A labelled form control, label above. */
export function FormField({
  label,
  className,
  children,
}: {
  label: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1.5 text-xs font-medium text-muted-foreground", className)}>
      {label}
      {children}
    </label>
  );
}

/** A quiet line standing in for a list with nothing in it. */
export function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-border/70 px-4 py-5 text-center text-xs text-muted-foreground">
      {children}
    </p>
  );
}

/** A grouped list of rows. */
export function RowList({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <Panel divided={false} className={className}>
      <ul className="divide-y divide-border/50">{children}</ul>
    </Panel>
  );
}

/** Inline error under a form or action. */
export function FormError({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs text-destructive-foreground" role="alert">
      {children}
    </p>
  );
}
