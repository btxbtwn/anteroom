import type { MouseEvent, ReactElement, ReactNode } from "react";
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
  /** The view's right-hand panel (the listing inspector or the assistant's chats), `null` when it has none. */
  rightPanel: RightPanel | null;
  /** Replaces the crumbs, as the listings view's open-listing tabs. */
  children?: ReactNode;
}

export interface RightPanel {
  open: boolean;
  /** What the panel holds, as in "Hide the listing inspector". */
  name: string;
  /** The panel element's id. */
  controls: string;
  onToggle: () => void;
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

/**
 * T3 Code's seamless titlebar: a transparent bar over the columns, which run to
 * the window's top edge in their own colours. With the listings sidebar open the
 * toggle sits over the sidebar and the crumbs start 20px into the main pane.
 */
export function WorkspaceTopbar({ crumbs, sidebarOpen, onToggleSidebar, rightPanel, children }: Props) {
  return (
    <header className="workspace-topbar absolute inset-x-0 top-0 z-2 flex h-(--workspace-topbar-height) items-center gap-2 pr-2.5 pl-(--topbar-inset) select-none [--topbar-inset:--spacing(3)] max-[900px]:hidden [html.desktop-app_&]:[--topbar-inset:var(--traffic-light-inset)]">
      {/* The pywebview window drags by this layer; controls sit above it. */}
      <div className="pywebview-drag-region absolute inset-0" aria-hidden="true" />
      <div
        className={
          sidebarOpen
            ? "relative flex w-[calc(var(--sidebar-column-width)-var(--topbar-inset))] shrink-0 items-center"
            : "relative flex shrink-0 items-center"
        }
      >
        <PanelToggle
          pressed={sidebarOpen}
          label={sidebarOpen ? "Hide listings" : "Show listings"}
          controls="listings-sidebar"
          onToggle={onToggleSidebar}
        >
          {sidebarOpen ? <PanelLeftCloseIcon className="size-4" /> : <PanelLeftIcon className="size-4" />}
        </PanelToggle>
      </div>
      {children ? (
        // Empty space beside the children still drags the window.
        <div className={`pointer-events-none relative flex h-7 min-w-0 flex-1 items-center${sidebarOpen ? " pl-3" : ""}`}>
          {children}
        </div>
      ) : (
        <div
          className={`relative flex h-7 min-w-0 flex-1 items-center gap-2 text-sm leading-none tracking-tight${sidebarOpen ? " pl-3" : ""}`}
        >
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
      )}
      {rightPanel === null ? null : (
        <div className="relative ml-auto flex min-w-0 items-center gap-1">
          <PanelToggle
            pressed={rightPanel.open}
            label={`${rightPanel.open ? "Hide" : "Show"} ${rightPanel.name}`}
            controls={rightPanel.controls}
            onToggle={rightPanel.onToggle}
          >
            {rightPanel.open ? <PanelRightCloseIcon className="size-4" /> : <PanelRightIcon className="size-4" />}
          </PanelToggle>
        </div>
      )}
    </header>
  );
}
