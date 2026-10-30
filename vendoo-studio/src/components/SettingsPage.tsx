import { useWorkspaceRuntime } from "../app/runtime";
import { useEffect } from "react";
import {
  DEFAULT_SETTINGS_SECTION,
  type SettingsSectionId,
} from "./settingsNav";
import { AgentSettings } from "./settings/AgentSettings";
import { AssistantSettings } from "./settings/AssistantSettings";
import { ConnectionsSettings } from "./settings/ConnectionsSettings";
import { DataSettings } from "./settings/DataSettings";
import { GeneralSettings } from "./settings/GeneralSettings";
import { HelpSettings } from "./settings/HelpSettings";
import { ListingsSettings } from "./settings/ListingsSettings";
import { LogsSettings } from "./settings/LogsSettings";
import { ProvidersSettings } from "./settings/ProvidersSettings";
import { SettingsPageContainer } from "./settings/SettingsLayout";

function scrollToSettingsTarget(targetId: string) {
  const target = document.getElementById(targetId);
  if (!target) return false;
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({
    behavior: prefersReducedMotion ? "auto" : "smooth",
    block: "center",
  });
  target.focus({ preventScroll: true });
  target.classList.remove("settings-search-target-pulse");
  if (!prefersReducedMotion) {
    void target.offsetWidth;
    target.classList.add("settings-search-target-pulse");
    target.addEventListener("blur", () => target.classList.remove("settings-search-target-pulse"), { once: true });
  }
  return true;
}

export function SettingsPage({
  section = DEFAULT_SETTINGS_SECTION,
  targetId = null,
  onTargetHandled,
  onOpenSetupGuide,
  onOpenListing,
}: {
  onOpenListing: (id: string) => void;
  section?: SettingsSectionId;
  targetId?: string | null;
  onTargetHandled?: () => void;
  onOpenSetupGuide?: () => void;
}) {
  const { hosted } = useWorkspaceRuntime();
  if (hosted && section !== "listings") section = "providers";
  useEffect(() => {
    if (!targetId) return;
    const frame = window.requestAnimationFrame(() => {
      if (scrollToSettingsTarget(targetId)) onTargetHandled?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [section, targetId, onTargetHandled]);

  return (
    <SettingsPageContainer className={section === "providers" ? "@container/providers" : undefined}>
      {section === "general" ? <GeneralSettings onOpenSetupGuide={onOpenSetupGuide} /> : null}
      {section === "listings" ? <ListingsSettings onOpenListing={onOpenListing} /> : null}
      {section === "assistant" ? <AssistantSettings /> : null}
      {section === "providers" ? <ProvidersSettings /> : null}
      {section === "connections" ? <ConnectionsSettings /> : null}
      {section === "agents" ? <AgentSettings /> : null}
      {section === "data" ? <DataSettings /> : null}
      {section === "logs" ? <LogsSettings /> : null}
      {section === "help" ? <HelpSettings /> : null}
    </SettingsPageContainer>
  );
}
