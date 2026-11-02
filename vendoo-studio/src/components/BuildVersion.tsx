import { useEffect } from "react";
import { frontendBuildState, reloadForBackend } from "./buildStamp";
import { useWorkspaceRuntime } from "../app/runtime";
import { toastManager } from "./ui/toast";

/** Desktop reloads once for an update; hosted tabs let the seller choose when. */
export function useReloadStaleFrontend(backendVersion: string | null | undefined) {
  const { hosted } = useWorkspaceRuntime();
  const outdated = frontendBuildState(__STUDIO_BUILD__, backendVersion).outdated;
  useEffect(() => {
    if (outdated && hosted) {
      const id = toastManager.add({
        type: "info",
        title: "Web update available",
        description: "Finish your work, then reload to use the latest version.",
        timeout: 0,
        actionProps: { children: "Reload app", onClick: () => window.location.reload() },
      });
      return () => toastManager.close(id);
    }
    if (outdated && reloadForBackend(backendVersion ?? "", window.sessionStorage)) {
      window.location.reload();
    }
  }, [outdated, backendVersion, hosted]);
}
