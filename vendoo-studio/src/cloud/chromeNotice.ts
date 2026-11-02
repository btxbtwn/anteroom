/** What the Connect Chrome button and its panel say for each Bridge state. */
import type { CloudConnection } from "../api/cloudExtension";

export interface ChromeNotice { button: string; message: string[] }

/** `attempted` is true once the seller has pressed Connect Chrome on this page. */
export function chromeNotice(connection: CloudConnection | null, attempted: boolean): ChromeNotice {
  const version = connection?.extensionVersion ? `Anteroom Bridge ${connection.extensionVersion}` : "Anteroom Bridge";
  if (connection?.ok && connection.state === "connected") {
    if (connection.supportedActions?.includes("marketplace.actions")) {
      return { button: "Chrome connected", message: [`This Chrome tab is connected. ${version}.`] };
    }
    return { button: "Update Bridge", message: [
      `${version} is out of date and can’t list, update or delist from the web.`,
      "Open Anteroom on your Mac, choose Connect Chrome there to update it, then reload this page.",
    ] };
  }
  if (connection?.ok && connection.state === "pending") {
    return { button: "Approve in Chrome…", message: ["Choose Connect browser in the Anteroom Bridge window. Keep this page open."] };
  }
  if (connection?.ok && attempted) {
    return { button: "Connect Chrome", message: ["Chrome didn’t approve the connection. Choose Connect Chrome to try again."] };
  }
  return { button: "Connect Chrome", message: [
    "Anteroom Bridge isn’t answering in this Chrome.",
    "Open Anteroom on your Mac and choose Connect Chrome there to install it, then reload this page. Chrome on iPhone can’t run the Bridge.",
  ] };
}
