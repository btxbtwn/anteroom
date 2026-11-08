import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { applyTheme, normalizeTheme, type ThemePreference } from "../theme";

/**
 * The preference to apply once Settings has answered. Until then the page keeps
 * the cached one `index.html` applied, so a slow read never flips the theme to
 * the default and back.
 */
export function savedThemePreference(settings: { theme?: unknown } | undefined): ThemePreference | null {
  return settings ? normalizeTheme(settings.theme) : null;
}

/** Keep the `.dark` class in sync with Settings → Appearance (and the device when System). */
export function ThemeSync() {
  const { data } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const preference = savedThemePreference(data);

  useEffect(() => {
    if (preference) applyTheme(preference);
  }, [preference]);

  useEffect(() => {
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference]);

  return null;
}
