import { expect, test as base, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

// A 1x1 white PNG, enough for an upload; the sandbox provider never reads photos.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC",
  "base64",
);

export const test = base.extend<{ phone: boolean; createConversation: (data: { title: string; notes: string }) => Promise<APIResponse> }>({
  // Device projects share a sandbox; remove only the inventory items this test creates.
  createConversation: async ({ request }, use) => {
    const status = await (await request.get("/api/status")).json();
    expect(status.sandbox, "browser tests only run against the sandbox").toBe(true);
    const ids: string[] = [];
    await use(async data => {
      const response = await request.post("/api/conversations", { data });
      if (response.ok()) ids.push((await response.json()).id);
      return response;
    });
    for (const id of ids.reverse()) {
      const response = await request.delete(`/api/conversations/${id}`);
      expect(response.ok(), "Remove this test's inventory item").toBe(true);
    }
  },
  phone: async ({ isMobile }, use) => {
    await use(isMobile);
  },
  // Any uncaught error in the page fails the test, so a broken screen cannot pass quietly.
  page: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await use(page);
    expect(errors, "uncaught errors in the page").toEqual([]);
  },
});

export { expect };

/** Record fixture sales through the same ledger API sellers use, after the one-time import. */
export async function recordSale(request: APIRequestContext, conversationId: string, amounts: {
  marketplace: string; item_revenue: string; item_cost: string; fees: string | null; shipping_cost: string;
  sold_on?: string | null;
}) {
  const response = await request.post(`/api/conversations/${conversationId}/marketplace-listings/sold`, { data: {
    currency: "USD", shipping_credit: "0", refunds: "0", ...amounts,
    sold_on: amounts.sold_on ?? new Date().toISOString().slice(0, 10), request_id: randomUUID().replaceAll("-", ""),
  } });
  expect(response.ok(), await response.text()).toBe(true);
  const { sale } = await response.json();
  if (amounts.sold_on === null) {
    const undated = await request.put(`/api/sales-ledger/${sale.id}/financials`, { data: {
      ...sale.financials, sold_on: null, expected_revision: sale.financial_revision, expected_order_basis: sale.order_basis,
    } });
    expect(undated.ok(), await undated.text()).toBe(true);
    return undated.json();
  }
  return sale;
}

/** Create a listing with one photo through the API and return its id. */
export async function createListing(request: APIRequestContext, title: string): Promise<string> {
  const status = await (await request.get("/api/status")).json();
  expect(status.sandbox, "browser tests only run against the sandbox").toBe(true);
  const conversation = await (await request.post("/api/conversations", { data: { title } })).json();
  const upload = await request.post(`/api/conversations/${conversation.id}/photos`, {
    multipart: { files: { name: "photo.png", mimeType: "image/png", buffer: PIXEL } },
  });
  expect(upload.ok()).toBe(true);
  return conversation.id;
}

/** Open a listing from the inventory; on a phone the inventory sits behind the navigation menu. */
export async function openListing(page: Page, phone: boolean, title: string): Promise<void> {
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
}

/** The editor's Title box; on a phone it is on the Listing tab. */
export async function titleBox(page: Page, phone: boolean) {
  if (phone) await page.getByRole("tab", { name: "Listing" }).click();
  return page.getByRole("group", { name: "General listing fields" }).getByRole("textbox", { name: "Title", exact: true });
}
