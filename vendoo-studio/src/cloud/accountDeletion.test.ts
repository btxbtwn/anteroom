import { describe, expect, it } from "vitest";
import { resolveConfirmDialogCopy } from "../ui/confirmDialog";
import { ACCOUNT_DELETION_MESSAGE } from "./accountDeletion";

describe("account deletion confirmation", () => {
  it("asks first, then says what goes and that live listings stay", () => {
    const copy = resolveConfirmDialogCopy(ACCOUNT_DELETION_MESSAGE);
    expect(copy.title).toBe("Delete your Anteroom account?");
    expect(copy.description).toContain("can't be undone");
    expect(copy.description).toContain("does not delist them");
  });
});
