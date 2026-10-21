import type { MouseEvent, ReactElement } from "react";
import { PanelLeftCloseIcon, PanelLeftIcon, PanelRightCloseIcon, PanelRightIcon } from "lucide-react";
import { Toggle } from "./ui/toggle";
import { Tooltip, TooltipPopup, TooltipTrigger } from "./ui/tooltip";
import type { WorkspaceCrumbs } from "./workspaceCrumbs";

/** A titlebar click must not start a window drag on the desktop build. */
export function stopTitlebarDrag(event: MouseEvent<HTMLElement>) {
  event.stopPropagation();
}

interface Props {
  crumbs: WorkspaceCrumbs;
  /** Listings sidebar is open. */
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  /** `null` when the view has no inspector to toggle. */
  detailOpen: boolean | null;
  onToggleDetail: () => void;
}

/** A ghost panel toggle with a tooltip, as T3 Code's PanelLayoutControls. */
function PanelToggle({
  pressed,
  label,
  controls,
  onToggle,
  children,
}: {
  pressed: boolean;
  label: string;
  controls: string;
  onToggle: () => void;
  children: ReactElement;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Toggle
            className="shrink-0 [-webkit-app-region:no-drag]"
            pressed={pressed}
            onPressedChange={onToggle}
            aria-label={label}
            aria-expanded={pressed}
            aria-controls={controls}
            variant="ghost"
            size="sm"
            onMouseDown={stopTitlebarDrag}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipPopup side="bottom">{label}</TooltipPopup>
    </Tooltip>
  );
}

/** One window-wide titlebar like T3 Code: crumbs left, panel toggles right. */
export function WorkspaceTopbar({ crumbs, sidebarOpen, onToggleSidebar, detailOpen, onToggleDetail }: Props) {
  return (
    <header className="workspace-topbar relative z-2 flex h-(--workspace-topbar-height) w-full shrink-0 items-center gap-2 bg-sidebar pr-2.5 pl-3 select-none max-[900px]:hidden [html.desktop-app_&]:pl-[90px]">
      {/* The pywebview window drags by this layer; controls sit above it. */}
      <div className="pywebview-drag-region absolute inset-0" aria-hidden="true" />
      <PanelToggle
        pressed={sidebarOpen}
        label={sidebarOpen ? "Hide listings" : "Show listings"}
        controls="listings-sidebar"
        onToggle={onToggleSidebar}
      >
        {sidebarOpen ? <PanelLeftCloseIcon className="size-4" /> : <PanelLeftIcon className="size-4" />}
      </PanelToggle>
      <div className="relative flex h-7 min-w-0 flex-1 items-center gap-2 text-sm leading-none tracking-tight">
        <span className="shrink-0 text-muted-foreground">{crumbs.context}</span>
        {crumbs.title ? (
          <>
            <span className="shrink-0 text-muted-foreground/60" aria-hidden="true">/</span>
            <span className="min-w-0 truncate font-medium text-foreground" title={crumbs.title}>
              {crumbs.title}
            </span>
          </>
        ) : null}
      </div>
      {detailOpen === null ? null : (
        <div className="relative ml-auto flex min-w-0 items-center gap-1">
          <PanelToggle
            pressed={detailOpen}
            label={detailOpen ? "Hide the listing inspector" : "Show the listing inspector"}
            controls="listing-inspector"
            onToggle={onToggleDetail}
          >
            {detailOpen ? <PanelRightCloseIcon className="size-4" /> : <PanelRightIcon className="size-4" />}
          </PanelToggle>
        </div>
      )}
    </header>
  );
}
