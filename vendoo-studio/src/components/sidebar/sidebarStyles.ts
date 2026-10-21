/**
 * T3 Code's SidebarMenuButton look (components/ui/sidebar.tsx, MIT, (c) 2026
 * T3 Tools Inc.) for Studio's own sidebar controls. `data-active="true"` marks
 * the selected row.
 */
export const SIDEBAR_ROW =
  "flex h-8 w-full cursor-pointer items-center gap-[var(--sidebar-control-gap)] overflow-hidden rounded-[var(--control-radius)] px-[var(--sidebar-row-content-inset)] text-left text-sm font-medium text-sidebar-muted-foreground/80 outline-hidden ring-ring transition-colors hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-inset active:bg-sidebar-row-active disabled:pointer-events-none disabled:opacity-64 data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-[var(--sidebar-icon-color)] hover:[&>svg]:text-sidebar-foreground data-[active=true]:[&>svg]:text-sidebar-foreground";

/** Square icon button in the sidebar footer, same states as SIDEBAR_ROW. */
export const SIDEBAR_ICON_BUTTON =
  "relative inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-[var(--control-radius)] text-[var(--sidebar-icon-color)] outline-hidden ring-ring transition-colors hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-inset disabled:pointer-events-none disabled:opacity-40 data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground [&_svg]:size-4";
