import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InventoryImport } from "./InventoryImport";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useQuery.mockReturnValue({ isSuccess: true, data: { total: 2, has_more: false, rows: [
    { id: "123456789012", title: "Already saved coat", price: "45.50", quantity: 3, conversation_id: "local-coat", reason: null },
    { id: "123456789013", title: "Shirt with variations", price: "20.00", quantity: 5, conversation_id: null,
      reason: "Variation listings are not supported by this import yet." },
  ] } });
});

describe("direct eBay inventory import", () => {
  it("shows existing and unsupported listings without offering to publish or replace them", () => {
    const html = renderToStaticMarkup(<InventoryImport marketplace="ebay" accountKey="seller:connection-one" onOpenListing={vi.fn()} />);
    expect(html).toContain("Already saved coat");
    expect(html).toContain("Open item");
    expect(html).toContain("3 available");
    expect(html).toContain("Variation listings are not supported");
    expect(html).toContain("does not change or publish listings");
    expect(html).not.toContain("Open in Vendoo");
    const checkbox = (name: string) => html.match(new RegExp(`<[^>]*role="checkbox"[^>]*aria-label="${name}"[^>]*>`))?.[0];
    expect(checkbox("Select Already saved coat")).toContain('aria-disabled="true"');
    expect(checkbox("Select Shirt with variations")).toContain('aria-disabled="true"');
  });

  it("keeps manual import available when loading the active list fails", () => {
    useQuery.mockReturnValue({ error: new Error("The active list could not be read.") });
    const html = renderToStaticMarkup(<InventoryImport marketplace="ebay" accountKey="seller:connection-one" onOpenListing={vi.fn()} />);
    expect(html).toContain("The active list could not be read.");
    expect(html).toContain("eBay listing ID");
    expect(html).toContain("Import listing");
  });

  it("offers a stop after the current listing while importing", () => {
    useMutation.mockReturnValue({ mutate: vi.fn(), isPending: true });
    const html = renderToStaticMarkup(<InventoryImport marketplace="ebay" accountKey="seller:connection-one" onOpenListing={vi.fn()} />);
    expect(html).toContain("Stop after this listing");
    expect(html).toContain("Importing listings and photos");
  });
});

describe("direct Etsy inventory import", () => {
  it("uses Etsy's endpoint and separates its account cache from eBay", async () => {
    const { api } = await import("../api/client");
    const candidates = vi.spyOn(api.etsy, "importCandidates").mockResolvedValue({ rows: [], page: 1, total: 0, has_more: false });
    const html = renderToStaticMarkup(<InventoryImport marketplace="etsy" accountKey="42:1" onOpenListing={vi.fn()} />);
    expect(html).toContain("Etsy listing ID");
    expect(html).toContain("active physical Etsy listings in USD without variations");
    expect(html).toContain('pattern="[1-9][0-9]{0,19}"');
    const options = useQuery.mock.calls[useQuery.mock.calls.length - 1][0];
    expect(options.queryKey).toEqual(["inventory-import", "etsy", "42:1", 1]);
    await options.queryFn();
    expect(candidates).toHaveBeenCalledWith(1);
    candidates.mockRestore();
  });

  it("routes selected imports exclusively to Etsy", async () => {
    const { api } = await import("../api/client");
    const submit = vi.spyOn(api.etsy, "importListing").mockRejectedValue(new Error("Unsupported variation listing"));
    const ebay = vi.spyOn(api.ebay, "importListing");
    renderToStaticMarkup(<InventoryImport marketplace="etsy" accountKey="42:1" onOpenListing={vi.fn()} />);
    await useMutation.mock.calls[useMutation.mock.calls.length - 1][0].mutationFn([{ id: "555" }]);
    expect(submit).toHaveBeenCalledWith("555");
    expect(ebay).not.toHaveBeenCalled();
    submit.mockRestore();
    ebay.mockRestore();
  });
});

describe("Chrome marketplace inventory import", () => {
  it("asks the seller to review a likely match instead of selecting it with the page", () => {
    useQuery.mockReturnValue({ isSuccess: true, data: { total: null, has_more: true, next_cursor: "next-1", rows: [
      { id: "bbbbbbbbbbbbbbbbbbbbbbbb", title: "Denim jacket", price: "45.00", quantity: 1, conversation_id: null, reason: null,
        match: { conversation_id: "local-jacket", title: "Levi's denim jacket", score: 0.9, price: 80, price_differs: true } },
    ] } });
    const html = renderToStaticMarkup(<InventoryImport marketplace="poshmark" accountKey="poshmark" onOpenListing={vi.fn()} />);
    expect(html).toContain("Poshmark listing link or ID");
    expect(html).toContain("Reads your Poshmark closet through Chrome");
    expect(html).toContain("Looks like “Levi&#x27;s denim jacket” in Anteroom (priced $80 there)");
    expect(html).toContain("Link to this item");
    expect(html).toContain("does not change or publish listings");
    // "Select this page" has nothing to select: the only row waits for review.
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Select this page<\/button>/);
  });

  it("reads Chrome pages by cursor and links a reviewed match through the Chrome import", async () => {
    const { api } = await import("../api/client");
    const candidates = vi.spyOn(api.chromeImport, "importCandidates").mockResolvedValue({ rows: [], total: null, has_more: false, next_cursor: null });
    const submit = vi.spyOn(api.chromeImport, "importListing").mockResolvedValue({
      conversation_id: "local-jacket", listing_id: "4401", reused: true, linked: true, photo_count: 3, warnings: [] });
    const etsy = vi.spyOn(api.etsy, "importListing");
    renderToStaticMarkup(<InventoryImport marketplace="vinted" accountKey="vinted" onOpenListing={vi.fn()} />);
    const options = useQuery.mock.calls[useQuery.mock.calls.length - 1][0];
    expect(options.queryKey).toEqual(["inventory-import", "vinted", "vinted", 1, null]);
    await options.queryFn();
    expect(candidates).toHaveBeenCalledWith("vinted", null);
    const mutation = useMutation.mock.calls[useMutation.mock.calls.length - 1][0];
    await mutation.mutationFn([{ id: "4401", link: "local-jacket" }, { id: "4402" }]);
    expect(submit).toHaveBeenNthCalledWith(1, "vinted", "4401", "local-jacket");
    expect(submit).toHaveBeenNthCalledWith(2, "vinted", "4402", undefined);
    expect(etsy).not.toHaveBeenCalled();
    candidates.mockRestore();
    submit.mockRestore();
    etsy.mockRestore();
  });
});
