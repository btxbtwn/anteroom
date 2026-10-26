import { Fragment, type ComponentType, type ReactNode } from "react";
import {
  ArrowLeftIcon,
  BoxIcon,
  CalendarClockIcon,
  ChartColumnIcon,
  GaugeIcon,
  MessageCircleIcon,
  SettingsIcon,
  StoreIcon,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { UpdateButton } from "../UpdateButton";
import type { WorkspaceView } from "../workspaceCrumbs";
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ROW } from "./sidebarStyles";

/** Footer geometry: SIDEBAR_ICON_BUTTON is size-8, the row is gap-0.5, padded by --sidebar-content-inset. */
const FOOTER_BUTTON_PX = 32;
const FOOTER_GAP_PX = 2;
const FOOTER_PADDING_PX = 8;
/** The sidebar's border-right. */
const SIDEBAR_BORDER_PX = 1;

/**
 * The footer buttons in seller-workflow order: home first, then the day's work
 * (sourcing), marketplace tools, then insight (analytics, plan usage),
 * then settings and the updater last, the way T3 Code ends its sidebar footer
 * with settings. The listings above are the inventory; Vendoo sync lives in
 * Settings → Data and on Analytics.
 */
export const SIDEBAR_FOOTER_BUTTONS = [
  "assistant",
  "sourcing",
  "analytics",
  "marketplaces",
  "automations",
  "usage",
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
  onCloseSettings,
  onOpenSourcing,
  onOpenAnalytics,
  onOpenMarketplaces,
  onOpenAutomations,
  onOpenAssistant,
  onOpenSettings,
  onOpenUsage,
}: {
  settingsMode: boolean;
  activeView: WorkspaceView;
  onCloseSettings: () => void;
  onOpenSourcing: () => void;
  onOpenAnalytics: () => void;
  onOpenMarketplaces: () => void;
  onOpenAutomations: () => void;
  onOpenAssistant: () => void;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
}) {
  const buttons: Record<FooterButtonId, ReactNode> = {
    assistant: (
      <FooterButton label="Assistant" icon={MessageCircleIcon} active={activeView === "assistant"} onClick={onOpenAssistant} />
    ),
    sourcing: <FooterButton label="Sourcing" icon={BoxIcon} active={activeView === "sourcing"} onClick={onOpenSourcing} />,
    analytics: (
      <FooterButton label="Analytics" icon={ChartColumnIcon} active={activeView === "analytics"} onClick={onOpenAnalytics} />
    ),
    marketplaces: <FooterButton label="Marketplaces" icon={StoreIcon} active={activeView === "marketplaces"} onClick={onOpenMarketplaces} />,
    automations: <FooterButton label="Automations" icon={CalendarClockIcon} active={activeView === "automations"} onClick={onOpenAutomations} />,
    usage: <FooterButton label="Usage" icon={GaugeIcon} active={activeView === "usage"} onClick={onOpenUsage} />,
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
