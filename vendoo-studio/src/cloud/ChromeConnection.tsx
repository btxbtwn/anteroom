import { useEffect, useRef, useState } from "react";
import { cloudExtension, type BrowserAccount, type CloudConnection } from "../api/cloudExtension";
import { Popover, PopoverPopup } from "../components/ui/popover";
import { chromeNotice } from "./chromeNotice";
import { startChromeRelay } from "./chromeRelay";

export function ChromeConnection({ account }: { account: BrowserAccount }) {
  const [bridge] = useState(() => cloudExtension(account));
  const [connection, setConnection] = useState<CloudConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
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
  const connectedNow = connection?.ok === true && connection.state === "connected";
  const capabilities = (connection?.supportedActions ?? []).join(",");
  // While this tab is connected, it carries the seller's approved marketplace commands and reads to Chrome.
  useEffect(() => connectedNow ? startChromeRelay(bridge, capabilities.split(",")) : undefined, [bridge, connectedNow, capabilities]);
  async function connect() {
    setBusy(true); setAttempted(true); setExpanded(true);
    try { setConnection(await bridge.connect()); } finally { setBusy(false); }
  }
  async function disconnect() {
    setBusy(true);
    try { setConnection(await bridge.disconnect()); } finally { setBusy(false); }
  }
  const pending = connection?.ok && connection.state === "pending";
  const notice = chromeNotice(connection, attempted);
  return <div className="chrome-connection"><div ref={anchor} className="connection-controls">
    <button type="button" disabled={busy || pending} onClick={() => connectedNow ? setExpanded(value => !value) : void connect()}>
      {busy && !pending ? "Connecting…" : notice.button}
    </button>
    {connectedNow && <button type="button" className="subtle" disabled={busy} onClick={() => void disconnect()}>Disconnect</button>}
  </div>
  <Popover open={expanded} onOpenChange={(open, details) => {
    // The buttons beside the panel open and close it themselves.
    if (!open && details.reason === "outside-press" && details.event.target instanceof Node && anchor.current?.contains(details.event.target)) return;
    setExpanded(open);
  }}>
    <PopoverPopup anchor={anchor} align="end" sideOffset={8} width="md" touchBackdrop>
      <div className="connection-help" role="status">{notice.message.map(line => <p key={line}>{line}</p>)}</div>
    </PopoverPopup>
  </Popover></div>;
}
