import { describe, expect, it } from "vitest";
import { installVersionMessage } from "./PreviousVersionsDialog";

describe("installVersionMessage", () => {
  it("says listings stay put when the database works with that build", () => {
    const message = installVersionMessage({ action: "keep", tag: "studio-macos-v0.1.594-abc1234", version: "0.1.594", title: null });
    expect(message).toContain("Anteroom 0.1.594");
    expect(message).toContain("stay as they are");
  });

  it("names the backup and warns about later changes when the layout is older", () => {
    const message = installVersionMessage({
      action: "restore",
      tag: "studio-macos-v0.1.590-abc1234",
      version: "0.1.590",
      title: null,
      snapshot: { name: "vendoo_studio-20261028-120000-pre-migration.db.gz", taken_at: "2026-10-28T12:00:00+00:00", reason: "pre-migration", size_bytes: 1 },
    });
    expect(message).toContain("older database layout");
    expect(message).toContain("won't appear in this version");
    expect(message).toContain("pre-rollback");
  });
});
