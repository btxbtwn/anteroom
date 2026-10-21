import { describe, expect, it } from "vitest";
import { consumeResponseSse } from "./chatStream";

describe("consumeResponseSse", () => {
  it("keeps agent events out of the reply text", async () => {
    const agent = JSON.stringify({
      id: "comps-sold-1",
      label: "Sold comps",
      status: "done",
      detail: "Found 8 sold comps",
      started_at: 1,
      ended_at: 2,
    });
    const body = [
      "event: agent",
      `data: ${agent}`,
      "",
      "data: Error: Photo analysis failed.",
      "",
      "data: [DONE]",
      "",
    ].join("\n");
    const { parts, sawDone } = await consumeResponseSse(new Response(body), () => {});
    expect(sawDone).toBe(true);
    expect(parts.content).toBe("Error: Photo analysis failed.");
    expect(parts.content).not.toContain("Sold comps");
  });
});
