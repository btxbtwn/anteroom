import { useEffect } from "react";
import { frontendBuildState, reloadForBackend } from "./buildStamp";

/** Reload once when this window's interface is behind the installed Studio. */
export function useReloadStaleFrontend(backendVersion: string | null | undefined) {
  const outdated = frontendBuildState(__STUDIO_BUILD__, backendVersion).outdated;
  useEffect(() => {
    if (outdated && reloadForBackend(backendVersion ?? "", window.sessionStorage)) {
      window.location.reload();
    }
  }, [outdated, backendVersion]);
}
