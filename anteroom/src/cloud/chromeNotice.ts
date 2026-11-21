/** What the Connect Anteroom Extension button and its panel say for each Bridge state. */
import { version as BRIDGE_VERSION } from "../../../anteroom-extension/manifest.json";
import { CHROME_WEB_STORE_URL } from "../api/chromeBridge";
import type { CloudConnection } from "../api/cloudExtension";

export interface ChromeNoticeAction { label: string; href: string }
export interface ChromeNotice { button: string; message: string[]; action?: ChromeNoticeAction }

/** True when `reported` is an older Bridge than the one built with this site. Unknown versions pass. */
function bridgeIsOlder(reported: string, expected: string = BRIDGE_VERSION): boolean {
  const parts = (version: string) => version.split(".").map(Number);
  const have = parts(reported), want = parts(expected);
  if (!reported || [...have, ...want].some(part => !Number.isInteger(part))) return false;
  for (let index = 0; index < Math.max(have.length, want.length); index += 1) {
    const difference = (have[index] ?? 0) - (want[index] ?? 0);
    if (difference) return difference < 0;
  }
  return false;
}

/** `attempted` is true once the seller has pressed Connect Anteroom Extension on this page. */
export function chromeNotice(connection: CloudConnection | null, attempted: boolean): ChromeNotice {
  const version = connection?.extensionVersion ? `Anteroom Bridge ${connection.extensionVersion}` : "Anteroom Bridge";
  if (connection?.ok && connection.state === "connected") {
    if (!connection.supportedActions?.includes("marketplace.actions")) {
      return {
        button: "Update Bridge",
        message: [
          `${version} is out of date and can’t list, update or delist from the web.`,
          "Update Anteroom Bridge from the Chrome Web Store, then reload this page.",
        ],
        action: { label: "Open Chrome Web Store", href: CHROME_WEB_STORE_URL },
      };
    }
    // Bridge fixes reach Chrome only when its copy is updated and reloaded, so an older copy keeps old bugs.
    if (bridgeIsOlder(connection.extensionVersion)) {
      return {
        button: "Update Bridge",
        message: [
          `${version} is older than this site’s ${BRIDGE_VERSION}, so fixes made since then aren’t running in Chrome yet.`,
          "Update Anteroom Bridge from the Chrome Web Store, then reload this page.",
        ],
        action: { label: "Open Chrome Web Store", href: CHROME_WEB_STORE_URL },
      };
    }
    return { button: "Chrome connected", message: [`This account stays connected in Chrome until you disconnect or sign out. ${version}.`,
      "Keep an Anteroom tab open in Chrome for marketplace reads and actions."] };
  }
  if (connection?.ok && connection.state === "pending") {
    return { button: "Approve in Chrome…", message: ["Choose Connect browser in the Anteroom Bridge window. Keep this page open."] };
  }
  if (connection?.ok && attempted) {
    return { button: "Connect Anteroom Extension", message: ["Chrome didn’t approve the connection. Choose Connect Anteroom Extension to try again."] };
  }
  return {
    button: "Connect Anteroom Extension",
    message: [
      "Anteroom Bridge isn’t answering in this Chrome.",
      "Install Anteroom Bridge from the Chrome Web Store, reload this page, and choose Connect Anteroom Extension. Chrome on iPhone can’t run the Bridge.",
    ],
    action: { label: "Install from Chrome Web Store", href: CHROME_WEB_STORE_URL },
  };
}
