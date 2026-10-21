import type { ComponentType, ReactNode } from "react";
import {
  ArrowLeftIcon,
  BoxIcon,
  ChartColumnIcon,
  ListIcon,
  MegaphoneIcon,
  MessageCircleIcon,
  SettingsIcon,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { UpdateButton } from "../UpdateButton";
import { VendooImportButton } from "../VendooImportButton";
import type { WorkspaceView } from "../workspaceCrumbs";
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ROW } from "./sidebarStyles";

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
            className={cn(SIDEBAR_ICON_BUTTON, "max-w-8 min-w-6 flex-[1_1_0]")}
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
  onOpenSettings: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center p-(--sidebar-content-inset) max-[900px]:hidden">
      <div className="flex w-full flex-wrap items-center gap-0.5">
        {settingsMode ? (
          <button type="button" className={cn(SIDEBAR_ROW, "w-auto text-xs")} aria-label="Back" onClick={onCloseSettings}>
            <ArrowLeftIcon />
            <span>Back</span>
          </button>
        ) : (
          <>
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
            <VendooImportButton />
            <FooterButton label="Sourcing" icon={BoxIcon} active={activeView === "sourcing"} onClick={onOpenSourcing} />
            <FooterButton label="Analytics" icon={ChartColumnIcon} active={activeView === "analytics"} onClick={onOpenAnalytics} />
            <FooterButton label="Marketing" icon={MegaphoneIcon} active={activeView === "marketing"} onClick={onOpenMarketing} />
            <FooterButton label="Assistant" icon={MessageCircleIcon} active={activeView === "assistant"} onClick={onOpenAssistant} />
            <FooterButton label="Settings" icon={SettingsIcon} onClick={onOpenSettings} />
          </>
        )}
        <UpdateButton />
      </div>
    </div>
  );
}
