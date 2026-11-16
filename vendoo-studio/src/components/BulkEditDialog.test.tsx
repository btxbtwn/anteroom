import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { BulkEditDialog } from "./BulkEditDialog";

const fixture = vi.hoisted(() => ({ state: 0, review: { edits: {}, rows: [{
  conversation_id: "fixture", title: "Fixture shirt", skip_reason: null,
  expected_revision_id: "revision", expected_basis: "basis", expected_result: "result",
  changes: [
    { field: "brand", old: "Old", new: "New" },
    { field: "poshmark_specifics", old: { brand: "Old", description: "Kept private detail" }, new: { description: "Kept private detail" } },
    { field: "_seller_fields", old: ["title"], new: ["brand", "title"] },
  ],
}] } }));
vi.mock("react", async (original) => ({ ...await original<typeof import("react")>(),
  useState: (initial: unknown) => [fixture.state++ === 7 ? fixture.review : initial, vi.fn()],
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: {} }), useQueryClient: () => ({}),
  useMutation: () => ({ isPending: false, data: undefined }),
}));
vi.mock("./PriceDropParts", () => ({ PriceDialog: ({ children }: { children: React.ReactNode }) => children }));
vi.mock("./ui/checkbox", () => ({ Checkbox: () => null }));
vi.mock("./ui/button", () => ({ Button: () => null }));

it("shows actual nested changes without unchanged objects or internal metadata", () => {
  fixture.state = 0;
  const html = renderToStaticMarkup(createElement(BulkEditDialog, { conversationIds: ["fixture"], onClose: vi.fn() }));
  expect(html).toContain("Poshmark · brand");
  expect(html).toContain("Old → Empty");
  expect(html).not.toContain("Kept private detail");
  expect(html).not.toContain("seller fields");
  expect(html).not.toContain("&quot;brand&quot;");
});
