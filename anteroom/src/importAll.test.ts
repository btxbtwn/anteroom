import { afterEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceImportHeld, MarketplaceImportPage, MarketplaceImportResult } from "./api/inventoryImport";
import { clearImportAll, importAllRun, resolveImportAllReview, startImportAll, stopImportAll, type Importer } from "./importAll";

type Row = MarketplaceImportPage["rows"][number];
const row = (id: string, extra: Partial<Row> = {}): Row =>
  ({ id, title: `Item ${id}`, price: "10.00", quantity: 1, reason: null, conversation_id: null, match: null, ...extra });
const result = (id: string, extra: Partial<MarketplaceImportResult> = {}): MarketplaceImportResult =>
  ({ conversation_id: `local-${id}`, listing_id: id, reused: false, photo_count: 2, warnings: [], ...extra });
const match = { conversation_id: "local-coat", title: "Wool coat", score: 0.9, price: 40, price_differs: false };

function fakeImporter(pages: MarketplaceImportPage[]) {
  const candidates = vi.fn(async (page: number, _cursor: string | null) => pages[page - 1]);
  const importNew = vi.fn(async (id: string): Promise<MarketplaceImportResult | MarketplaceImportHeld> => result(id));
  const importListing = vi.fn();
  return { importer: { candidates, importNew, importListing } satisfies Importer, candidates, importNew, importListing };
}

function held<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((resolve) => { release = resolve; });
  return { promise, release };
}

async function finished(key: string) {
  await vi.waitFor(() => expect(["done", "stopped", "failed"]).toContain(importAllRun(key)?.status));
  return importAllRun(key)!;
}

afterEach(() => clearImportAll());

describe("import all active listings", () => {
  it("walks every page, imports new listings and leaves suggested matches for the seller", async () => {
    const { importer, candidates, importNew, importListing } = fakeImporter([
      { total: null, has_more: true, next_cursor: "c2", rows: [row("a"), row("b", { conversation_id: "local-b" })] },
      { total: null, has_more: false, next_cursor: null, rows: [row("c", { match }), row("d", { reason: "Not USD." }), row("e")] },
    ]);
    const onFinish = vi.fn();
    startImportAll("poshmark:me", "poshmark", importer, onFinish);
    const run = await finished("poshmark:me");

    expect(candidates.mock.calls).toEqual([[1, null], [2, "c2"]]);
    // Only listings with no local item and no suggested match are imported; nothing is linked on its own.
    expect(importNew.mock.calls).toEqual([["a"], ["e"]]);
    expect(importListing).not.toHaveBeenCalled();
    expect(run).toMatchObject({ status: "done", page: 2, imported: 2, alreadyIn: 1, skipped: 1, failures: [] });
    expect(run.review).toEqual([{ id: "c", title: "Item c", price: "10.00", match }]);
    expect(onFinish).toHaveBeenCalledOnce();
    // Once the seller links or imports it, it leaves the list for good.
    resolveImportAllReview("poshmark:me", "c");
    expect(importAllRun("poshmark:me")?.review).toEqual([]);
  });

  it("leaves a listing for review when the server finds a match as it saves", async () => {
    const { importer, importNew } = fakeImporter([{ total: 1, has_more: false, rows: [row("a")] }]);
    importNew.mockResolvedValueOnce({ conversation_id: null, listing_id: "a", match });
    startImportAll("mercari:me", "mercari", importer, vi.fn());
    const run = await finished("mercari:me");
    expect(run).toMatchObject({ imported: 0, review: [{ id: "a", match }] });
  });

  it("keeps going past a listing that fails and reports it", async () => {
    const { importer, importNew } = fakeImporter([{ total: 2, has_more: false, rows: [row("a"), row("b")] }]);
    importNew.mockRejectedValueOnce(new Error("Poshmark did not answer."));
    startImportAll("poshmark:me", "poshmark", importer, vi.fn());
    const run = await finished("poshmark:me");
    expect(run).toMatchObject({ status: "done", imported: 1, failures: [{ id: "a", title: "Item a", message: "Poshmark did not answer." }] });
  });

  it("keeps what an imported listing still needs, without counting it as a failure", async () => {
    const { importer, importNew } = fakeImporter([{ total: 2, has_more: false, rows: [row("a"), row("b")] }]);
    importNew.mockResolvedValueOnce(result("a", { warnings: ["Imported 2 of 4 photos. Add the missing photos before crosslisting."] }));
    startImportAll("ebay:me", "ebay", importer, vi.fn());
    expect(await finished("ebay:me")).toMatchObject({ imported: 2, failures: [],
      warnings: [{ id: "a", title: "Item a", message: "Imported 2 of 4 photos. Add the missing photos before crosslisting." }] });
  });

  it("counts a listing another tab already imported as already in Anteroom", async () => {
    const { importer, importNew } = fakeImporter([{ total: 1, has_more: false, rows: [row("a")] }]);
    importNew.mockResolvedValueOnce(result("a", { reused: true }));
    startImportAll("ebay:me", "ebay", importer, vi.fn());
    expect(await finished("ebay:me")).toMatchObject({ imported: 0, alreadyIn: 1 });
  });

  it("stops after the current listing when asked", async () => {
    const gate = held<MarketplaceImportResult>();
    const { importer, importNew } = fakeImporter([{ total: 3, has_more: true, rows: [row("a"), row("b"), row("c")] }]);
    importNew.mockImplementationOnce(() => gate.promise);
    startImportAll("ebay:me", "ebay", importer, vi.fn());
    await vi.waitFor(() => expect(importNew).toHaveBeenCalledOnce());
    stopImportAll("ebay:me");
    expect(importAllRun("ebay:me")?.status).toBe("stopping");
    gate.release(result("a"));
    expect(await finished("ebay:me")).toMatchObject({ status: "stopped", imported: 1 });
    expect(importNew).toHaveBeenCalledOnce();
  });

  it("treats a page that fails after the seller pressed stop as stopped", async () => {
    const gate = held<MarketplaceImportPage>();
    const { importer, candidates } = fakeImporter([]);
    candidates.mockImplementationOnce(() => gate.promise.then(() => { throw new Error("Chrome closed."); }));
    startImportAll("depop:me", "depop", importer, vi.fn());
    stopImportAll("depop:me");
    gate.release({ total: 0, has_more: false, rows: [] });
    expect(await finished("depop:me")).toMatchObject({ status: "stopped", error: null });
  });

  it("stops with the marketplace's message when a page cannot be read, keeping what was imported", async () => {
    const { importer, candidates } = fakeImporter([{ total: null, has_more: true, next_cursor: "c2", rows: [row("a")] }]);
    candidates.mockResolvedValueOnce({ total: null, has_more: true, next_cursor: "c2", rows: [row("a")] })
      .mockRejectedValueOnce(new Error("Open Chrome and sign in to Mercari."));
    startImportAll("mercari:me", "mercari", importer, vi.fn());
    expect(await finished("mercari:me")).toMatchObject({ status: "failed", page: 2, imported: 1, error: "Open Chrome and sign in to Mercari." });
  });

  it("runs marketplaces side by side; none waits for another", async () => {
    const gate = held<MarketplaceImportResult>();
    const first = fakeImporter([{ total: 1, has_more: false, rows: [row("a")] }]);
    first.importNew.mockImplementationOnce(() => gate.promise);
    const second = fakeImporter([{ total: 1, has_more: false, rows: [row("z")] }]);
    startImportAll("poshmark:me", "poshmark", first.importer, vi.fn());
    startImportAll("mercari:me", "mercari", second.importer, vi.fn());
    expect(await finished("mercari:me")).toMatchObject({ status: "done", imported: 1 });
    expect(importAllRun("poshmark:me")?.status).toBe("running");
    gate.release(result("a"));
    expect(await finished("poshmark:me")).toMatchObject({ status: "done", imported: 1 });
  });

  it("forgets a cleared run and stops its walk, so nothing carries over to the next seller", async () => {
    const gate = held<MarketplaceImportResult>();
    const { importer, importNew } = fakeImporter([{ total: 2, has_more: false, rows: [row("a"), row("b")] }]);
    importNew.mockImplementationOnce(() => gate.promise);
    const onFinish = vi.fn();
    startImportAll("etsy:shop-1", "etsy", importer, onFinish);
    await vi.waitFor(() => expect(importNew).toHaveBeenCalledOnce());
    clearImportAll();
    expect(importAllRun("etsy:shop-1")).toBeUndefined();
    gate.release(result("a"));
    await vi.waitFor(() => expect(onFinish).toHaveBeenCalledOnce());
    expect(importNew).toHaveBeenCalledOnce();
    expect(importAllRun("etsy:shop-1")).toBeUndefined();
  });

  it("clears another account's run on the same marketplace and keeps the current one", async () => {
    const never = { candidates: () => new Promise<MarketplaceImportPage>(() => {}), importNew: vi.fn(), importListing: vi.fn() };
    startImportAll("ebay:old", "ebay", never, vi.fn());
    startImportAll("ebay:new", "ebay", never, vi.fn());
    startImportAll("etsy:shop", "etsy", never, vi.fn());
    clearImportAll("ebay", "ebay:new");
    expect([importAllRun("ebay:old"), importAllRun("ebay:new")?.status, importAllRun("etsy:shop")?.status]).toEqual([undefined, "running", "running"]);
  });

  it("stops when listings keep failing in a row, instead of trying every one", async () => {
    const rows = Array.from({ length: 8 }, (_, index) => row(`r${index}`));
    const { importer, importNew } = fakeImporter([{ total: 8, has_more: false, rows }]);
    importNew.mockRejectedValue(new Error("Connect Chrome to import from Depop."));
    startImportAll("depop:me", "depop", importer, vi.fn());
    const run = await finished("depop:me");
    expect(run).toMatchObject({ status: "failed", error: "Connect Chrome to import from Depop." });
    expect(importNew).toHaveBeenCalledTimes(5);
  });

  it("reports a marketplace whose cursors go round in a circle, keeping what was imported", async () => {
    const pages: MarketplaceImportPage[] = [
      { total: null, has_more: true, next_cursor: "b", rows: [row("1")] },
      { total: null, has_more: true, next_cursor: "a", rows: [row("2")] },
      { total: null, has_more: true, next_cursor: "b", rows: [row("3")] },
    ];
    const { importer, candidates } = fakeImporter(pages);
    startImportAll("mercari:me", "mercari", importer, vi.fn());
    expect(await finished("mercari:me")).toMatchObject({ status: "failed", imported: 3,
      error: "Mercari said there were more listings but did not give the next page." });
    expect(candidates).toHaveBeenCalledTimes(3);
  });

  it("stops before reading another page once a different eBay account is connected", async () => {
    const { importer, candidates } = fakeImporter([
      { total: 50, has_more: true, rows: [row("a")] },
      { total: 50, has_more: false, rows: [row("b")] },
    ]);
    // Same account around page 1, then a different one before page 2.
    const account = vi.fn().mockResolvedValueOnce("seller-one:100").mockResolvedValueOnce("seller-one:100").mockResolvedValueOnce("seller-two:200");
    startImportAll("ebay:seller-one:100", "ebay", { ...importer, account }, vi.fn(), "seller-one:100");
    const run = await finished("ebay:seller-one:100");
    expect(run).toMatchObject({ status: "failed", imported: 1, page: 2 });
    expect(run.error).toContain("The connected eBay account changed");
    expect(candidates).toHaveBeenCalledOnce();
  });

  it("stops when the Etsy shop is disconnected mid-run", async () => {
    const { importer, candidates } = fakeImporter([{ total: 50, has_more: true, rows: [row("a")] }]);
    const account = vi.fn().mockResolvedValueOnce("shop:1").mockResolvedValueOnce("shop:1").mockResolvedValueOnce(null);
    startImportAll("etsy:shop:1", "etsy", { ...importer, account }, vi.fn(), "shop:1");
    expect(await finished("etsy:shop:1")).toMatchObject({ status: "failed", imported: 1 });
    expect(candidates).toHaveBeenCalledOnce();
  });

  it("keeps going while the same account stays connected", async () => {
    const { importer } = fakeImporter([
      { total: 2, has_more: true, rows: [row("a")] },
      { total: 2, has_more: false, rows: [row("b")] },
    ]);
    const account = vi.fn().mockResolvedValue("seller-one:100");
    startImportAll("ebay:seller-one:100", "ebay", { ...importer, account }, vi.fn(), "seller-one:100");
    expect(await finished("ebay:seller-one:100")).toMatchObject({ status: "done", imported: 2 });
    expect(account).toHaveBeenCalledTimes(4);
  });

  it("drops a page read while the account changed, importing nothing from it", async () => {
    const { importer, importNew } = fakeImporter([{ total: 1, has_more: false, rows: [row("from-new-account")] }]);
    const account = vi.fn().mockResolvedValueOnce("seller-one:100").mockResolvedValueOnce("seller-two:200");
    startImportAll("ebay:seller-one:100", "ebay", { ...importer, account }, vi.fn(), "seller-one:100");
    expect(await finished("ebay:seller-one:100")).toMatchObject({ status: "failed", imported: 0 });
    expect(importNew).not.toHaveBeenCalled();
  });

  it("stops at once when another account is connected before the first page is read", async () => {
    const { importer, candidates } = fakeImporter([{ total: 1, has_more: false, rows: [row("a")] }]);
    const account = vi.fn().mockResolvedValue("seller-two:200");
    startImportAll("ebay:seller-one:100", "ebay", { ...importer, account }, vi.fn(), "seller-one:100");
    const run = await finished("ebay:seller-one:100");
    expect(run).toMatchObject({ status: "failed", imported: 0 });
    expect(run.error).toContain("The connected eBay account changed");
    expect(candidates).not.toHaveBeenCalled();
  });

  it("reports a marketplace that says there is more but repeats its cursor", async () => {
    const { importer, candidates } = fakeImporter([
      { total: null, has_more: true, next_cursor: "c2", rows: [row("a")] },
      { total: null, has_more: true, next_cursor: "c2", rows: [row("a")] },
    ]);
    startImportAll("depop:me", "depop", importer, vi.fn());
    expect(await finished("depop:me")).toMatchObject({ status: "failed", imported: 1,
      error: "Depop said there were more listings but did not give the next page." });
    expect(candidates).toHaveBeenCalledTimes(2);
  });
});
