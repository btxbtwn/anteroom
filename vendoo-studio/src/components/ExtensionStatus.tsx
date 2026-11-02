type ExtensionStatusData = {
  connected?: boolean;
  up_to_date?: boolean;
  expected_version?: string | null;
  version?: string | null;
  load_path?: string | null;
  duplicate_bridge?: boolean;
  runs_marketplace_actions?: boolean;
};

/** What Settings → Connections says about the listing extension. */
export function extensionConnection(data: ExtensionStatusData | undefined): {
  label: string;
  detail: string;
  tone: "success" | "warning" | "muted";
} {
  const connected = Boolean(data?.connected);
  // An earlier Bridge keeps working as long as it can run marketplace actions.
  const outdated = connected && data?.runs_marketplace_actions === false;
  const earlier = connected && data?.up_to_date === false;
  if (data?.duplicate_bridge) {
    return {
      label: "Two copies loaded",
      tone: "warning",
      detail: [
        "Chrome has more than one copy of the Anteroom Bridge, and they take turns on the connection, so form reads and listing checks can come back empty.",
        data?.load_path ? `Keep the copy at ${data.load_path} and remove the others.` : "Keep one copy and remove the others.",
        "Open chrome://extensions, remove the extra copy, then press Connect Chrome.",
      ].join(" "),
    };
  }
  if (outdated) {
    const expected = data?.expected_version || null;
    const running = data?.version || null;
    const loadPath = data?.load_path || null;
    return {
      label: "Outdated",
      tone: "warning",
      detail: [
        expected ? `This Anteroom build expects extension ${expected}.` : "This Anteroom build has a newer extension.",
        running ? `Chrome is running ${running}.` : "Chrome is not on Anteroom's copy yet.",
        loadPath ? `Load unpacked from ${loadPath}.` : "Load the unpacked folder below.",
        "Connect Chrome reloads that folder.",
      ].join(" "),
    };
  }
  if (earlier) {
    return {
      label: "Connected",
      tone: "success",
      detail: [
        data?.version ? `Chrome is running extension ${data.version}, which still works.` : "Chrome is running an earlier extension, which still works.",
        "Connect Chrome loads the newer copy when you want it.",
      ].join(" "),
    };
  }
  if (connected) {
    return {
      label: "Connected",
      tone: "success",
      detail: "Chrome is running this build's listing extension.",
    };
  }
  return {
    label: "Offline",
    tone: "muted",
    detail: "Chrome is not connected. Press Connect Chrome.",
  };
}
