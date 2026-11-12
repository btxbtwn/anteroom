/** What the Connect Anteroom Extension button and its panel say for each Bridge state. */
import { version as BRIDGE_VERSION } from "../../../vendoo-extension/manifest.json";
import type { CloudConnection } from "../api/cloudExtension";

export interface ChromeNotice { button: string; message: string[] }

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
      return { button: "Update Bridge", message: [
        `${version} is out of date and can’t list, update or delist from the web.`,
        "Open Anteroom on your computer, choose Connect Chrome there to update it, then reload this page.",
      ] };
    }
    // Bridge fixes reach Chrome only when its copy is updated and reloaded, so an older copy keeps old bugs.
    if (bridgeIsOlder(connection.extensionVersion)) {
      return { button: "Update Bridge", message: [
        `${version} is older than this site’s ${BRIDGE_VERSION}, so fixes made since then aren’t running in Chrome yet.`,
        "Open Anteroom on your computer and choose Connect Chrome there to update it, then reload this page.",
      ] };
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
  return { button: "Connect Anteroom Extension", message: [
    "Anteroom Bridge isn’t answering in this Chrome.",
    "Open Anteroom on your computer and choose Connect Chrome there to install it, then reload this page. Chrome on iPhone can’t run the Bridge.",
  ] };
}
