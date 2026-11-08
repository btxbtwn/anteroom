import { useEffect, useState } from "react";
import appIcon from "../../desktop/AppIcon.png";
import { Button } from "../components/ui/button";
import "../styles/startup.css";

/** Startup only: keep the same shell while session and workspace checks finish. */
export function StartupScreen({ error, onReload = () => window.location.reload() }: {
  error?: string;
  onReload?: () => void;
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 10_000);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <main className="startup-screen" aria-label="Opening Anteroom">
      <div className="startup-content">
        <img className="startup-logo" src={appIcon} width="48" height="48" alt="" />
        <h1 className="startup-title">Anteroom</h1>
        <div className="startup-status" role={error ? "alert" : "status"} aria-atomic="true">
          {!error && <span className="startup-spinner" aria-hidden="true" />}
          <p>{error || (slow ? "Taking longer than usual. You can keep waiting or reload." : "Opening your workspace…")}</p>
        </div>
        <div className="startup-recovery">
          {(slow || error) && <Button variant="outline" className="min-h-11" onClick={onReload}>Reload Anteroom</Button>}
        </div>
      </div>
    </main>
  );
}
