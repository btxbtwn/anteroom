import { UpdateButton } from "../UpdateButton";
import { VendooImportButton } from "../VendooImportButton";
import type { WorkspaceView } from "../workspaceCrumbs";
import {
  AnalyticsIcon,
  AssistantIcon,
  MarketingIcon,
  QueueIcon,
  SettingsIcon,
  SourcingIcon,
} from "./SidebarIcons";

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
    <div className="sidebar-footer">
      <div className="sidebar-footer-actions">
        {settingsMode ? (
          <button
            type="button"
            className="sidebar-back-btn"
            aria-label="Back"
            onClick={onCloseSettings}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>Back</span>
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`sidebar-icon-btn queue-nav${activeView === "queue" ? " selected" : ""}`}
              title={queueCount ? `Queue (${queueCount})` : "Queue"}
              aria-label={`Queue${queueCount ? ` (${queueCount})` : ""}`}
              aria-pressed={activeView === "queue"}
              onClick={onOpenQueue}
            >
              <QueueIcon />
              {queueCount ? <span className="queue-nav-dot" aria-hidden="true" /> : null}
            </button>
            <VendooImportButton />
            <button
              type="button"
              className={`sidebar-icon-btn${activeView === "sourcing" ? " selected" : ""}`}
              title="Sourcing"
              aria-label="Sourcing"
              aria-pressed={activeView === "sourcing"}
              onClick={onOpenSourcing}
            >
              <SourcingIcon />
            </button>
            <button
              type="button"
              className={`sidebar-icon-btn${activeView === "analytics" ? " selected" : ""}`}
              title="Analytics"
              aria-label="Analytics"
              aria-pressed={activeView === "analytics"}
              onClick={onOpenAnalytics}
            >
              <AnalyticsIcon />
            </button>
            <button
              type="button"
              className={`sidebar-icon-btn${activeView === "marketing" ? " selected" : ""}`}
              title="Marketing"
              aria-label="Marketing"
              aria-pressed={activeView === "marketing"}
              onClick={onOpenMarketing}
            >
              <MarketingIcon />
            </button>
            <button
              type="button"
              className={`sidebar-icon-btn${activeView === "assistant" ? " selected" : ""}`}
              title="Assistant"
              aria-label="Assistant"
              aria-pressed={activeView === "assistant"}
              onClick={onOpenAssistant}
            >
              <AssistantIcon />
            </button>
            <button
              type="button"
              className="sidebar-icon-btn"
              title="Settings"
              aria-label="Settings"
              onClick={onOpenSettings}
            >
              <SettingsIcon />
            </button>
          </>
        )}
        <UpdateButton />
      </div>
    </div>
  );
}
