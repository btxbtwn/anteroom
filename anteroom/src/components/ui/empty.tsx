// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc. Adapted only where it imported T3-specific modules.

import { cn } from "../../lib/utils";

const emptySizeClassName = {
  compact:
    "min-h-64 gap-4 p-6 md:p-10 [&_[data-slot=empty-media]]:mb-0 [&_[data-slot=empty-title]]:text-[1.0625rem] [&_[data-slot=empty-title]]:leading-6 [&_[data-slot=empty-description]]:text-[0.8125rem] [&_[data-slot=empty-description]]:leading-[1.125rem]",
  default: "gap-6 p-6 md:p-12",
  hero: "gap-6 p-6 md:p-12 [&_[data-slot=empty-title]]:text-2xl sm:[&_[data-slot=empty-title]]:text-3xl",
} as const;

function Empty({
  className,
  size = "default",
  ...props
}: React.ComponentProps<"div"> & { size?: keyof typeof emptySizeClassName }) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col items-center justify-center text-balance text-center",
        emptySizeClassName[size],
        className,
      )}
      data-slot="empty"
      {...props}
    />
  );
}

function EmptyHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex max-w-sm flex-col items-center text-center", className)}
      data-slot="empty-header"
      {...props}
    />
  );
}

function EmptyTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("font-semibold text-xl", className)} data-slot="empty-title" {...props} />
  );
}

function EmptyDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <div
      className={cn(
        "text-muted-foreground text-sm [&>a:hover]:text-primary [&>a]:underline [&>a]:underline-offset-4 [[data-slot=empty-title]+&]:mt-1 [[data-slot=empty-description]+&]:mt-1",
        className,
      )}
      data-slot="empty-description"
      {...props}
    />
  );
}

function EmptyContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex w-full min-w-0 max-w-sm flex-col items-center gap-4 text-balance text-sm",
        className,
      )}
      data-slot="empty-content"
      {...props}
    />
  );
}

export { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyContent };
