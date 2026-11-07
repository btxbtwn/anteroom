import { useWorkspaceRuntime } from "../../app/runtime";
import { Fragment, type ComponentType, type ReactNode } from "react";
import {
  ArrowLeftIcon,
  BoxIcon,
  CalendarClockIcon,
  ChartColumnIcon,
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
 * The footer pages in seller-workflow order: home first, then the day's work
 * (sourcing), marketplace tools, then insight (analytics), then settings, the
 * way T3 Code ends its sidebar footer with settings. The updater sits after
 * them. The listings above are the inventory; Vendoo sync lives in
 * Settings → Data and on Analytics.
 */
export const SIDEBAR_FOOTER_BUTTONS = [
  "assistant",
  "sourcing",
  "analytics",
  "marketplaces",
  "automations",
  "settings",
] as const;

type FooterButtonId = (typeof SIDEBAR_FOOTER_BUTTONS)[number];

/** The narrowest sidebar that shows every page button and the updater on one row. */
export const SIDEBAR_FOOTER_WIDTH =
  (SIDEBAR_FOOTER_BUTTONS.length + 1) * FOOTER_BUTTON_PX +
  SIDEBAR_FOOTER_BUTTONS.length * FOOTER_GAP_PX +
  2 * FOOTER_PADDING_PX +
  SIDEBAR_BORDER_PX;

/**
 * Icon-only on a narrow sidebar, icon and name once the sidebar has room
 * (the footer is a size container; see LABELED below), the way Linear keeps
 * its page links named. The tooltip carries the name either way.
 */
function FooterButton({
  label,
  page,
  icon: Icon,
  active,
  onClick,
}: {
  label: string;
  page?: keyof typeof pageLoaders;
  icon: ComponentType<{ className?: string }>;
  active?: boolean;
  onClick: () => void;
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
            className={cn(SIDEBAR_ICON_BUTTON, LABELED_BUTTON, "group/footer-page")}
            aria-label={label}
            aria-current={active ? "page" : undefined}
            data-active={active}
            onClick={onClick}
            disabled={disabled}
            {...intent}
          />
        }
      >
        <Icon />
        {/* The icon colour is too faint for text; the name uses the sidebar's muted text colour. */}
        <span className="hidden truncate text-sidebar-muted-foreground group-hover/footer-page:text-sidebar-foreground group-data-[active=true]/footer-page:text-sidebar-foreground @min-[18rem]/footer:inline">
          {label}
        </span>
      </TooltipTrigger>
      <TooltipPopup side="top">{label}</TooltipPopup>
    </Tooltip>
  );
}

/**
 * From 18rem (the default sidebar is wider) the six pages sit in two named
 * columns; below that they collapse to a row of icons.
 */
const LABELED_BUTTON =
  "@min-[18rem]/footer:w-full @min-[18rem]/footer:justify-start @min-[18rem]/footer:gap-2 @min-[18rem]/footer:px-2 @min-[18rem]/footer:text-xs @min-[18rem]/footer:font-medium";

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
    settings: <FooterButton page="settings" label="Settings" icon={SettingsIcon} onClick={onOpenSettings} />,
  };

  return (
    <div className="@container/footer flex shrink-0 items-center p-(--sidebar-content-inset) max-[900px]:hidden">
      {settingsMode ? (
        <div className="flex w-full items-center gap-0.5">
          <button type="button" className={cn(SIDEBAR_ROW, "w-auto text-xs")} aria-label="Back" onClick={onCloseSettings}>
            <ArrowLeftIcon />
            <span>Back</span>
          </button>
          {!runtime.hosted && <UpdateButton />}
        </div>
      ) : (
        <div className="flex w-full items-end gap-0.5">
          <nav aria-label="Pages" className="flex min-w-0 flex-1 items-center gap-0.5 @min-[18rem]/footer:grid @min-[18rem]/footer:grid-cols-2">
            {SIDEBAR_FOOTER_BUTTONS.map((id) => <Fragment key={id}>{buttons[id]}</Fragment>)}
          </nav>
          {runtime.hosted ? null : <UpdateButton />}
        </div>
      )}
    </div>
  );
}
