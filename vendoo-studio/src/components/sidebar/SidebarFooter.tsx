import { Fragment, type ComponentType, type ReactNode } from "react";
import {
  ArrowLeftIcon,
  BoxIcon,
  ChartColumnIcon,
  ListIcon,
  MegaphoneIcon,
  MessageCircleIcon,
  Rows3Icon,
  SettingsIcon,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { UpdateButton } from "../UpdateButton";
import { VendooImportButton } from "../VendooImportButton";
import type { WorkspaceView } from "../workspaceCrumbs";
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ROW } from "./sidebarStyles";

/** Footer geometry: SIDEBAR_ICON_BUTTON is size-8, the row is gap-0.5, padded by --sidebar-content-inset. */
const FOOTER_BUTTON_PX = 32;
const FOOTER_GAP_PX = 2;
const FOOTER_PADDING_PX = 8;
/** The sidebar's border-right. */
const SIDEBAR_BORDER_PX = 1;

/**
 * The footer buttons in seller-workflow order: home and inventory first, then
 * the day's work (Vendoo sync, sourcing, the send queue), then insight
 * (analytics, marketing), then settings and the updater last, the way T3 Code
 * ends its sidebar footer with settings.
 */
export const SIDEBAR_FOOTER_BUTTONS = [
  "assistant",
  "inventory",
  "sync",
  "sourcing",
  "queue",
  "analytics",
  "marketing",
  "settings",
  "update",
] as const;

type FooterButtonId = (typeof SIDEBAR_FOOTER_BUTTONS)[number];

/** The narrowest sidebar that shows every footer button on one row. */
export const SIDEBAR_FOOTER_WIDTH =
  SIDEBAR_FOOTER_BUTTONS.length * FOOTER_BUTTON_PX +
  (SIDEBAR_FOOTER_BUTTONS.length - 1) * FOOTER_GAP_PX +
  2 * FOOTER_PADDING_PX +
  SIDEBAR_BORDER_PX;

function FooterButton({
  label,
  icon: Icon,
  active,
  onClick,
  children,
}: {
  label: string;
  icon: ComponentType<{ className?: string }>;
  active?: boolean;
  onClick: () => void;
  children?: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            className={SIDEBAR_ICON_BUTTON}
            aria-label={label}
            aria-pressed={active === undefined ? undefined : active}
            data-active={active}
            onClick={onClick}
          />
        }
      >
        <Icon />
        {children}
      </TooltipTrigger>
      <TooltipPopup side="top">{label}</TooltipPopup>
    </Tooltip>
  );
}

/** The page buttons along the bottom of the sidebar, or Back while in settings. */
export function SidebarFooter({
  settingsMode,
  activeView,
  queueCount,
  onCloseSettings,
  onOpenQueue,
  onOpenSourcing,
  onOpenAnalytics,
  onOpenMarketing,
  onOpenAssistant,
  onOpenInventory,
  onOpenSettings,
}: {
  settingsMode: boolean;
  activeView: WorkspaceView;
  queueCount: number;
  onCloseSettings: () => void;
  onOpenQueue: () => void;
  onOpenSourcing: () => void;
  onOpenAnalytics: () => void;
  onOpenMarketing: () => void;
  onOpenAssistant: () => void;
  onOpenInventory: () => void;
  onOpenSettings: () => void;
}) {
  const buttons: Record<FooterButtonId, ReactNode> = {
    assistant: (
      <FooterButton label="Assistant" icon={MessageCircleIcon} active={activeView === "assistant"} onClick={onOpenAssistant} />
    ),
    inventory: (
      <FooterButton label="Inventory" icon={Rows3Icon} active={activeView === "listings"} onClick={onOpenInventory} />
    ),
    sync: <VendooImportButton />,
    sourcing: <FooterButton label="Sourcing" icon={BoxIcon} active={activeView === "sourcing"} onClick={onOpenSourcing} />,
    queue: (
      <FooterButton
        label={queueCount ? `Queue (${queueCount})` : "Queue"}
        icon={ListIcon}
        active={activeView === "queue"}
        onClick={onOpenQueue}
      >
        {queueCount ? (
          <span className="absolute top-1.25 right-1.25 size-1.25 rounded-full bg-primary" aria-hidden="true" />
        ) : null}
      </FooterButton>
    ),
    analytics: (
      <FooterButton label="Analytics" icon={ChartColumnIcon} active={activeView === "analytics"} onClick={onOpenAnalytics} />
    ),
    marketing: (
      <FooterButton label="Marketing" icon={MegaphoneIcon} active={activeView === "marketing"} onClick={onOpenMarketing} />
    ),
    settings: <FooterButton label="Settings" icon={SettingsIcon} onClick={onOpenSettings} />,
    update: <UpdateButton />,
  };

  return (
    <div className="flex shrink-0 items-center p-(--sidebar-content-inset) max-[900px]:hidden">
      <div className="flex w-full flex-wrap items-center gap-0.5">
        {settingsMode ? (
          <>
            <button type="button" className={cn(SIDEBAR_ROW, "w-auto text-xs")} aria-label="Back" onClick={onCloseSettings}>
              <ArrowLeftIcon />
              <span>Back</span>
            </button>
            <UpdateButton />
          </>
        ) : (
          SIDEBAR_FOOTER_BUTTONS.map((id) => <Fragment key={id}>{buttons[id]}</Fragment>)
        )}
      </div>
    </div>
  );
}
