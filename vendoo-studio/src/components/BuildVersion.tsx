import { useEffect } from "react";
import { frontendBuildState, reloadForBackend } from "./buildStamp";
import { StatusDot } from "./StatusDot";

export function BuildVersion({ backendVersion }: { backendVersion: string | null | undefined }) {
  const state = frontendBuildState(__STUDIO_BUILD__, backendVersion);
  // A browser that kept the old page shell shows the old interface against the
  // new backend. One reload fetches the current bundle; if the bundle really is
  // stale on disk the reload changes nothing and the status bar keeps saying so.
  useEffect(() => {
    if (state.outdated && reloadForBackend(backendVersion ?? "", window.sessionStorage)) {
      window.location.reload();
    }
  }, [state.outdated, backendVersion]);
  return (
    <span className="flex items-center gap-1.5">
      {state.outdated && <StatusDot tone="warning" />}
      <span className={state.outdated ? "text-warning-foreground" : undefined} title={state.title}>
        {state.label}
      </span>
    </span>
  );
}
