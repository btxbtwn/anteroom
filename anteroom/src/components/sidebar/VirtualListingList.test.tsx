import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { VirtualListingList } from "./VirtualListingList";

it("mounts only a viewport of rows even for a large inventory", () => {
  const rows = Array.from({ length: 5000 }, (_, index) => ({ id: String(index) }));
  const renderRow = vi.fn(row => <span>Item {row.id}</span>);
  const html = renderToStaticMarkup(<VirtualListingList rows={rows} renderRow={renderRow} />);
  expect(renderRow.mock.calls.length).toBeGreaterThan(0);
  expect(renderRow.mock.calls.length).toBeLessThan(30);
  expect(html).toContain('aria-setsize="5000"');
  expect(html).toContain("Item 0");
  expect(html).not.toContain("Item 4999");
});
