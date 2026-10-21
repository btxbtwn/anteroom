import { describe, expect, it } from "vitest";
import { readScroll } from "./useChatScroll";

const at = (top: number, height: number, client = 600) => ({ top, height, client });
const idle = { following: false, reading: false };

describe("readScroll", () => {
  it("pins near the bottom", () => {
    expect(readScroll(at(0, 2000), at(1350, 2000), idle)).toBe("pin");
  });

  it("unpins when the reader drags the scrollbar or presses a key to go up", () => {
    expect(readScroll(at(1400, 2000), at(900, 2000), { following: false, reading: true })).toBe("unpin");
  });

  it("stays pinned when the content grows under the transcript", () => {
    expect(readScroll(at(1400, 2000), at(1400, 2600), idle)).toBe("keep");
  });

  it("stays pinned when WebKit pulls the offset up while a turn is being written", () => {
    // Seen in WebKit on an iPhone-sized window: the offset dropped from the bottom to near the top, with nobody touching it.
    expect(readScroll(at(8061, 8524, 463), at(207, 9145, 463), idle)).toBe("keep");
    expect(readScroll(at(8879, 9346, 467), at(7951, 9346, 467), idle)).toBe("keep");
  });

  it("leaves a smooth scroll to the end alone", () => {
    expect(readScroll(at(1400, 2000), at(200, 3000), { following: true, reading: true })).toBe("keep");
  });
});
