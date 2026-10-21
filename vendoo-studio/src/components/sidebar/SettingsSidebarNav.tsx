import type { ComponentType } from "react";
import { BotIcon, CircleHelpIcon, DatabaseIcon, Link2Icon, ScrollTextIcon, Settings2Icon, TagIcon } from "lucide-react";
import { cn } from "../../lib/utils";
import { SETTINGS_NAV_ITEMS, SETTINGS_SECTION_LABELS, type SettingsSectionId } from "../settingsNav";
import { SIDEBAR_ROW } from "./sidebarStyles";
import type { SettingsSearch } from "./useSettingsSearch";

/* Section icons and result rows follow T3 Code's SettingsSidebarNav (MIT,
   (c) 2026 T3 Tools Inc.). */
const SECTION_ICONS: Record<SettingsSectionId, ComponentType<{ className?: string }>> = {
  general: Settings2Icon,
  listings: TagIcon,
  providers: BotIcon,
  connections: Link2Icon,
  data: DatabaseIcon,
  logs: ScrollTextIcon,
  help: CircleHelpIcon,
};

/**
 * The settings sections, or the search results while the settings search box
 * (rendered by ListingSidebar) holds a query.
 */
export function SettingsSidebarNav({
  search,
  settingsSection,
  onSettingsSectionChange,
}: {
  search: SettingsSearch;
  settingsSection: SettingsSectionId;
  onSettingsSectionChange: (section: SettingsSectionId) => void;
}) {
  const { activeResultIndex, setActiveResultIndex, settingsResults, isSettingsSearching, handleSettingsResult } = search;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-(--sidebar-content-inset) pb-2 max-[900px]:pb-4">
      {isSettingsSearching && settingsResults.length === 0 ? (
        <p role="status" className="px-2 py-6 text-center text-xs text-sidebar-muted-foreground">
          No settings found
        </p>
      ) : null}
      {isSettingsSearching ? (
        <div
          id="settings-search-results"
          role="listbox"
          aria-label="Settings search results"
          className="flex flex-col gap-1"
        >
          {settingsResults.map((item, index) => {
            const Icon = SECTION_ICONS[item.section];
            return (
              <button
                key={item.id}
                id={`settings-search-result-${item.id}`}
                type="button"
                role="option"
                aria-selected={index === activeResultIndex}
                data-active={index === activeResultIndex}
                className={cn(SIDEBAR_ROW, "h-auto min-h-10 items-start rounded-lg p-2 text-xs")}
                onMouseMove={() => setActiveResultIndex(index)}
                onClick={() => handleSettingsResult(item)}
              >
                <Icon className="h-5 size-3.5 shrink-0 text-sidebar-muted-foreground/60" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-sidebar-foreground">{item.title}</span>
                  <span className="block truncate text-2xs text-sidebar-muted-foreground/75">
                    {SETTINGS_SECTION_LABELS[item.section]}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <nav aria-label="Settings sections" className="flex flex-col gap-1">
          {SETTINGS_NAV_ITEMS.map((item) => {
            const Icon = SECTION_ICONS[item.id];
            return (
              <button
                key={item.id}
                type="button"
                data-active={settingsSection === item.id}
                aria-current={settingsSection === item.id ? "page" : undefined}
                className={SIDEBAR_ROW}
                onClick={() => onSettingsSectionChange(item.id)}
              >
                <Icon />
                <span className="truncate">{item.label}</span>
              </button>
            );
          })}
        </nav>
      )}
    </div>
  );
}
