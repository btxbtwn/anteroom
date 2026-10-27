import { describe, expect, it } from "vitest";
import { attachmentUrl } from "./TimelineRows";
import { STRIP_THUMB_SIZE } from "../../photoThumb";

describe("attachmentUrl", () => {
  it("serves the original unless a grid asks for a thumbnail", () => {
    expect(attachmentUrl("abc.png")).toBe("/api/chat-attachments/abc.png");
    expect(attachmentUrl("a b.png", STRIP_THUMB_SIZE)).toBe(
      `/api/chat-attachments/a%20b.png?size=${STRIP_THUMB_SIZE}`,
    );
  });
});
