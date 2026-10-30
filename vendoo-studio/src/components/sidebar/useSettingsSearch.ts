import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useWorkspaceRuntime } from "../../app/runtime";
import { useVendooEnabled } from "../../vendooMode";
import { searchSettings, settingsSearchItemsForMode, type SettingsSearchItem } from "../settingsNav";

export type SettingsSearch = ReturnType<typeof useSettingsSearch>;

/**
 * The settings sidebar's search box: its query and highlighted result, "/" to
 * focus it while settings are open, and arrow/Enter/Escape inside it. Called by
 * ListingSidebar, so leaving settings clears the query the same way it always has.
 */
export function useSettingsSearch(
  settingsMode: boolean,
  onSettingsSearchResult: (item: SettingsSearchItem) => void,
) {
  const [settingsQuery, setSettingsQuery] = useState("");
  const [activeResultIndex, setActiveResultIndex] = useState(0);
  const settingsSearchRef = useRef<HTMLInputElement>(null);
  const { hosted } = useWorkspaceRuntime();
  const vendooOn = useVendooEnabled();
  const settingsResults = useMemo(
    () => searchSettings(settingsQuery, settingsSearchItemsForMode(vendooOn).filter((item) => !hosted || (item.section === "listings" && item.id !== "vendoo") || (item.section === "providers" && !["chatgpt", "claude", "cursor", "brave"].includes(item.id)))),
    [settingsQuery, vendooOn, hosted],
  );
  const isSettingsSearching = settingsQuery.trim().length > 0;

  useEffect(() => {
    if (!settingsMode) {
      setSettingsQuery("");
      setActiveResultIndex(0);
    }
  }, [settingsMode]);

  useEffect(() => {
    setActiveResultIndex((index) => Math.min(index, Math.max(settingsResults.length - 1, 0)));
  }, [settingsResults.length]);

  useEffect(() => {
    if (!settingsMode) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          target.closest('[role="dialog"], [aria-modal="true"]') !== null)
      ) {
        return;
      }
      event.preventDefault();
      settingsSearchRef.current?.focus();
      settingsSearchRef.current?.select();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [settingsMode]);

  const clearSettingsSearch = useCallback(() => {
    setSettingsQuery("");
    setActiveResultIndex(0);
  }, []);

  const handleSettingsResult = useCallback(
    (item: SettingsSearchItem) => {
      clearSettingsSearch();
      onSettingsSearchResult(item);
    },
    [clearSettingsSearch, onSettingsSearchResult],
  );

  const handleSettingsSearchKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape" && isSettingsSearching) {
        event.preventDefault();
        event.stopPropagation();
        clearSettingsSearch();
        return;
      }
      if (settingsResults.length === 0) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveResultIndex((index) => (index + 1) % settingsResults.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveResultIndex((index) => (index - 1 + settingsResults.length) % settingsResults.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const result = settingsResults[activeResultIndex];
        if (result) handleSettingsResult(result);
      }
    },
    [activeResultIndex, clearSettingsSearch, handleSettingsResult, isSettingsSearching, settingsResults],
  );

  return {
    settingsSearchRef,
    settingsQuery,
    setSettingsQuery,
    activeResultIndex,
    setActiveResultIndex,
    settingsResults,
    isSettingsSearching,
    clearSettingsSearch,
    handleSettingsResult,
    handleSettingsSearchKeyDown,
  };
}
