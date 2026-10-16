import { SETTINGS_NAV_ITEMS, SETTINGS_SECTION_LABELS, type SettingsSectionId } from "../settingsNav";
import { SettingsSectionIcon } from "./SidebarIcons";
import type { SettingsSearch } from "./useSettingsSearch";

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
    <div className="sidebar-list settings-sidebar-nav">
      {isSettingsSearching && settingsResults.length === 0 ? (
        <div className="sidebar-empty">No settings found</div>
      ) : null}
      {isSettingsSearching ? (
        <div id="settings-search-results" role="listbox" aria-label="Settings search results">
          {settingsResults.map((item, index) => (
            <button
              key={item.id}
              id={`settings-search-result-${item.id}`}
              type="button"
              role="option"
              aria-selected={index === activeResultIndex}
              className={`settings-nav-result${index === activeResultIndex ? " selected" : ""}`}
              onMouseEnter={() => setActiveResultIndex(index)}
              onClick={() => handleSettingsResult(item)}
            >
              <span className="settings-nav-result-title">{item.title}</span>
              <span className="settings-nav-result-section">{SETTINGS_SECTION_LABELS[item.section]}</span>
            </button>
          ))}
        </div>
      ) : (
        <nav aria-label="Settings sections">
          {SETTINGS_NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`settings-nav-item${settingsSection === item.id ? " selected" : ""}`}
              onClick={() => onSettingsSectionChange(item.id)}
            >
              <SettingsSectionIcon section={item.id} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}
