import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { BulkMarketplaceDialog } from "./BulkMarketplaceDialog";

const { useMutation, invalidateQueries } = vi.hoisted(() => ({ useMutation: vi.fn(), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQueryClient: () => ({ invalidateQueries }) }));
vi.mock("./PriceDropParts", () => ({
  PriceDialog: ({ title, description, children, footer }: { title: string; description: string; children: React.ReactNode; footer: React.ReactNode }) => <div><h2>{title}</h2><p>{description}</p>{children}{footer}</div>,
}));

beforeEach(() => {
  useMutation.mockReset();
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
});

describe("bulk marketplace dialog", () => {
  it("names no marketplace by default and sends nothing until reviewed", () => {
    const perform = vi.spyOn(api.bulkMarketplace, "perform");
    const preview = vi.spyOn(api.bulkMarketplace, "preview");
    const html = renderToStaticMarkup(<BulkMarketplaceDialog conversationIds={["a", "b"]} onClose={vi.fn()} />);
    expect(html).toContain("Marketplace actions for 2 items");
    const single = renderToStaticMarkup(<BulkMarketplaceDialog conversationIds={["a"]} onClose={vi.fn()} />);
    expect(single).toContain("Marketplace actions for 1 item</h2>");
    expect(html).toContain(">Update<");
    expect(html).not.toContain("Items run one at a time");
    expect(html).toContain("List and Update can run together");
    expect(html).toContain("other actions run one item at a time");
    expect(html).toContain("Mark as not listed");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Review items<\/button>/);
    expect(html.match(/role="checkbox"[^>]*aria-checked="(?:true|false)"/g)).toHaveLength(5);
    expect(html).not.toMatch(/role="checkbox"[^>]*aria-checked="true"/);
    expect(perform).not.toHaveBeenCalled();
    expect(preview).not.toHaveBeenCalled();
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });

  it("pre-selects the marketplace when opened from a marketplace tab without bypassing review", async () => {
    const perform = vi.spyOn(api.bulkMarketplace, "perform");
    const preview = vi.spyOn(api.bulkMarketplace, "preview").mockResolvedValue({ action: "list", marketplaces: ["depop"], rows: [] });
    const html = renderToStaticMarkup(
      <BulkMarketplaceDialog conversationIds={["a", "b"]} initialAction="list" initialMarketplaces={["depop"]} onClose={vi.fn()} />,
    );
    expect(html.match(/role="checkbox"[^>]*aria-checked="true"/g)).toHaveLength(1);
    expect(html).toContain("Pre-selected from this tab");
    expect(html).toContain(">Review items</button>");
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>Review items<\/button>/);
    expect(perform).not.toHaveBeenCalled();
    expect(preview).not.toHaveBeenCalled();
    // Clicking Review items sends only the pre-selected marketplace to preview, never others.
    const previewOptions = useMutation.mock.calls[0]?.[0] as { mutationFn: (ids: string[]) => Promise<unknown> };
    await previewOptions.mutationFn(["a", "b"]);
    expect(preview).toHaveBeenCalledWith(["a", "b"], "list", ["depop"]);
    expect(perform).not.toHaveBeenCalled();
  });
});
