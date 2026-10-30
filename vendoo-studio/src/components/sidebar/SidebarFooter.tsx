import { useWorkspaceRuntime } from "../../app/runtime";
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
import { pageLoaders } from "../../app/pageLoaders";
import { useNavigationIntent } from "../../lib/useNavigationIntent";

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
  page,
  icon: Icon,
  active,
  onClick,
  children,
}: {
  label: string;
  page?: keyof typeof pageLoaders;
  icon: ComponentType<{ className?: string }>;
  active?: boolean;
  onClick: () => void;
  children?: ReactNode;
}) {
  const runtime = useWorkspaceRuntime();
  const disabled = Boolean(page && !runtime.views.has(page));
  const intent = useNavigationIntent(() => {
    if (page) void pageLoaders[page]().catch(() => {});
  });
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
            disabled={disabled}
            {...intent}
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
  const runtime = useWorkspaceRuntime();
  const buttons: Record<FooterButtonId, ReactNode> = {
    assistant: (
      <FooterButton page="assistant" label="Assistant" icon={MessageCircleIcon} active={activeView === "assistant"} onClick={onOpenAssistant} />
    ),
    sourcing: <FooterButton page="sourcing" label="Sourcing" icon={BoxIcon} active={activeView === "sourcing"} onClick={onOpenSourcing} />,
    analytics: (
      <FooterButton page="analytics" label="Analytics" icon={ChartColumnIcon} active={activeView === "analytics"} onClick={onOpenAnalytics} />
    ),
    marketplaces: <FooterButton page="marketplaces" label="Marketplaces" icon={StoreIcon} active={activeView === "marketplaces"} onClick={onOpenMarketplaces} />,
    automations: <FooterButton page="automations" label="Automations" icon={CalendarClockIcon} active={activeView === "automations"} onClick={onOpenAutomations} />,
    usage: <FooterButton page="usage" label="Usage" icon={GaugeIcon} active={activeView === "usage"} onClick={onOpenUsage} />,
    settings: <FooterButton page="settings" label="Settings" icon={SettingsIcon} onClick={onOpenSettings} />,
    update: runtime.hosted ? null : <UpdateButton />,
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
            {!runtime.hosted && <UpdateButton />}
          </>
        ) : (
          SIDEBAR_FOOTER_BUTTONS.map((id) => <Fragment key={id}>{buttons[id]}</Fragment>)
        )}
      </div>
    </div>
  );
}
