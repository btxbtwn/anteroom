import { useEffect, useSyncExternalStore } from "react";
import { frontendBuildState, reloadForBackend } from "./buildStamp";
import { useWorkspaceRuntime } from "../app/runtime";
import { toastManager } from "./ui/toast";
import { hasUnsavedWork, subscribeUnsavedWork } from "../lib/unsavedWork";

/**
 * Desktop reloads once for an update, but never over edits that have not
 * saved yet: it waits until they have. Hosted tabs let the seller choose when.
 */
export function useReloadStaleFrontend(backendVersion: string | null | undefined) {
  const { hosted } = useWorkspaceRuntime();
  const outdated = frontendBuildState(__STUDIO_BUILD__, backendVersion).outdated;
  const unsaved = useSyncExternalStore(subscribeUnsavedWork, hasUnsavedWork, hasUnsavedWork);
  useEffect(() => {
    if (!outdated || !hosted) return;
    const id = toastManager.add({
      type: "info",
      title: "Web update available",
      description: "Finish your work, then reload to use the latest version.",
      timeout: 0,
      actionProps: { children: "Reload app", onClick: () => window.location.reload() },
    });
    return () => toastManager.close(id);
  }, [outdated, hosted]);
  useEffect(() => {
    if (hosted || !outdated || unsaved) return;
    if (reloadForBackend(backendVersion ?? "", window.sessionStorage)) window.location.reload();
  }, [outdated, backendVersion, hosted, unsaved]);
}
