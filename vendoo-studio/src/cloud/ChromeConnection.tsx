import { useEffect, useState } from "react";
import { cloudExtension, type BrowserAccount, type CloudConnection } from "../api/cloudExtension";

export function ChromeConnection({ account }: { account: BrowserAccount }) {
  const [bridge] = useState(() => cloudExtension(account));
  const [connection, setConnection] = useState<CloudConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let active = true;
    const check = () => { void bridge.status().then(status => { if (active) setConnection(status); }); };
    check();
    window.addEventListener("focus", check);
    return () => { active = false; window.removeEventListener("focus", check); };
  }, [bridge]);
  useEffect(() => {
    if (connection?.state !== "pending" && connection?.state !== "connected") return;
    let active = true;
    const interval = window.setInterval(() => { void bridge.status().then(status => { if (active) setConnection(status); }); }, connection.state === "pending" ? 1500 : 15000);
    return () => { active = false; window.clearInterval(interval); };
  }, [bridge, connection?.state]);
  async function connect() {
    setBusy(true); setExpanded(true);
    try { setConnection(await bridge.connect()); } finally { setBusy(false); }
  }
  async function disconnect() {
    setBusy(true);
    try { setConnection(await bridge.disconnect()); } finally { setBusy(false); }
  }
  const connected = connection?.ok && connection.state === "connected";
  const pending = connection?.ok && connection.state === "pending";
  return <div className="chrome-connection"><div className="connection-controls">
    <button type="button" disabled={busy || pending} onClick={() => connected ? setExpanded(value => !value) : void connect()}>
      {connected ? "Chrome connected" : pending ? "Approve in Chrome…" : busy ? "Connecting…" : "Connect Chrome"}
    </button>
    {connected && <button type="button" className="subtle" disabled={busy} onClick={() => void disconnect()}>Disconnect</button>}
  </div>{expanded && <div className="connection-help" role="status">
    {connected ? <p>This Chrome tab is connected to your account.{connection.extensionVersion ? ` Bridge ${connection.extensionVersion}.` : ""}</p>
      : pending ? <p>Choose Connect browser in the Anteroom Bridge window. Keep this page open.</p>
      : <><p>Use desktop Chrome with Anteroom Bridge 0.2.38 or later.</p><p>Using the local Anteroom app? Update it and choose Connect Chrome there, then keep its managed Bridge enabled. Standalone web-test ZIPs do not support local marketplace actions. For web-only use, load the updated Bridge in chrome://extensions, then reload this page.</p></>}
  </div>}</div>;
}
