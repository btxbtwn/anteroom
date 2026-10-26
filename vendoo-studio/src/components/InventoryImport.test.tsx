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
    expect(html).toMatch(/aria-label="Select Already saved coat"[^>]*disabled/);
    expect(html).toMatch(/aria-label="Select Shirt with variations"[^>]*disabled/);
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
    await useMutation.mock.calls[useMutation.mock.calls.length - 1][0].mutationFn(["555"]);
    expect(submit).toHaveBeenCalledWith("555");
    expect(ebay).not.toHaveBeenCalled();
    submit.mockRestore();
    ebay.mockRestore();
  });
});
