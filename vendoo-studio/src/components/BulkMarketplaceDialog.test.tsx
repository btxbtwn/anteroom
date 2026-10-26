import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { BulkMarketplaceDialog } from "./BulkMarketplaceDialog";

const { useMutation, invalidateQueries } = vi.hoisted(() => ({ useMutation: vi.fn(), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQueryClient: () => ({ invalidateQueries }) }));
vi.mock("./PriceDropParts", () => ({
  PriceDialog: ({ title, children, footer }: { title: string; children: React.ReactNode; footer: React.ReactNode }) => <div><h2>{title}</h2>{children}{footer}</div>,
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
    expect(html).toContain("List, delist or relist 2 items");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Review items<\/button>/);
    expect(html).not.toContain('aria-checked="true"');
    expect(perform).not.toHaveBeenCalled();
    expect(preview).not.toHaveBeenCalled();
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });
});
