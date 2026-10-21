type ExtensionStatusData = {
  connected?: boolean;
  up_to_date?: boolean;
  expected_version?: string | null;
  version?: string | null;
  load_path?: string | null;
};

/** What Settings → Connections says about the listing extension. */
export function extensionConnection(data: ExtensionStatusData | undefined): {
  label: string;
  detail: string;
  tone: "success" | "warning" | "muted";
} {
  const connected = Boolean(data?.connected);
  const outdated = connected && data?.up_to_date === false;
  if (outdated) {
    const expected = data?.expected_version || null;
    const running = data?.version || null;
    const loadPath = data?.load_path || null;
    return {
      label: "Outdated",
      tone: "warning",
      detail: [
        expected ? `This Studio build expects extension ${expected}.` : "This Studio build has a newer extension.",
        running ? `Chrome is running ${running}.` : "Chrome is not on Studio's copy yet.",
        loadPath ? `Load unpacked from ${loadPath}.` : "Load the unpacked folder below.",
        "Connect Chrome reloads that folder so it stays in lockstep.",
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
