import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AutoRelistSection } from "./AutoRelistSection";
import { ScheduledDraftsSection } from "./ScheduledDraftsSection";
import { SaleDetectionTracker } from "./settings/SaleDetectionTracker";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery,
  useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null });
});

describe("Wave 2 Track C automation and sale-sync UI components", () => {
  it("renders ScheduledDraftsSection with scheduled drafts, failure alerts, and audit history", () => {
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => {
      if (queryKey[0] === "scheduled-drafts") {
        return {
          isLoading: false,
          data: [
            {
              conversation_id: "conv-1",
              title: "Patagonia Fleece",
              ready_to_list: true,
              scheduled_publish_at: "2026-12-01T16:00:00Z",
              scheduled_marketplaces: ["poshmark", "ebay"],
              scheduled_revision_id: "rev-1",
              scheduled_confirmed_at: "2026-11-22T15:00:00Z",
              scheduled_status: "scheduled",
              scheduled_error: null,
            },
            {
              conversation_id: "conv-2",
              title: "Nike Running Shoes",
              ready_to_list: true,
              scheduled_publish_at: null,
              scheduled_marketplaces: ["mercari"],
              scheduled_revision_id: "rev-2",
              scheduled_confirmed_at: null,
              scheduled_status: "failed",
              scheduled_error: "Mercari session expired.",
            },
          ],
        };
      }
      if (queryKey[0] === "scheduled-draft-logs") {
        return {
          isLoading: false,
          data: [
            {
              id: "log-1",
              conversation_id: "conv-2",
              title: "Nike Running Shoes",
              revision_id: "rev-2",
              marketplaces: ["mercari"],
              scheduled_for: "2026-11-22T14:00:00Z",
              status: "failed",
              results: null,
              error: "Mercari session expired.",
              created_at: "2026-11-22T14:00:05Z",
              finished_at: "2026-11-22T14:00:05Z",
            },
          ],
        };
      }
      return { isLoading: false, data: [] };
    });

    const html = renderToStaticMarkup(<ScheduledDraftsSection onOpenListing={() => undefined} />);
    expect(html).toContain("Scheduled draft publishing");
    expect(html).toContain("Patagonia Fleece");
    expect(html).toContain("Nike Running Shoes");
    expect(html).toContain("Mercari session expired.");
    expect(html).toContain("1 scheduled draft failed to publish");
    expect(html).toContain("Scheduled publish history (1)");
  });

  it("renders AutoRelistSection with per-marketplace controls OFF by default and eBay GTC mode", () => {
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => {
      if (queryKey[0] === "auto-relist-rules") {
        return {
          isLoading: false,
          data: [
            {
              marketplace: "poshmark",
              enabled: false,
              min_age_days: 30,
              ebay_recreate_before_gtc_minutes: null,
              daily_cap: 25,
              price_adjust_mode: null,
              price_adjust_value: null,
              price_floor: null,
              filters: {},
              relisted_today: 0,
              remaining_today: 25,
              last_run_at: null,
            },
            {
              marketplace: "ebay",
              enabled: false,
              min_age_days: 30,
              ebay_recreate_before_gtc_minutes: 30,
              daily_cap: 50,
              price_adjust_mode: "percent",
              price_adjust_value: 10,
              price_floor: 20,
              filters: {},
              relisted_today: 2,
              remaining_today: 48,
              last_run_at: null,
            },
          ],
        };
      }
      return { isLoading: false, data: [] };
    });

    const html = renderToStaticMarkup(<AutoRelistSection />);
    expect(html).toContain("Auto-relist and eBay auto-recreate");
    expect(html).toContain("Minimum 30-day age cycle");
    expect(html).toContain("Recreates ~30 min before 30-day GTC renewal");
    expect(html).toContain("Drop 10% (floor $20)");
    expect(html).toContain("Cap 2/50 today");
  });

  it("renders SaleDetectionTracker with 60-day summary, SKU, error details, and one-click Retry", () => {
    useQuery.mockImplementation(() => ({
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
      data: {
        windowDays: 60,
        autodelist: true,
        autoAdjustQuantity: true,
        summary: { totalSales: 1, succeeded: 1, failed: 1, pending: 0, skipped: 0 },
        entries: [
          {
            saleId: "sale-1",
            conversationId: "conv-1",
            title: "3-Pack Wool Socks",
            sku: "SKU-WOOL-3",
            soldOn: "ebay",
            orderId: "ord-123",
            orderStatus: "paid",
            source: "poll",
            quantity: 1,
            stockStatus: "remaining",
            remainingQuantity: 2,
            soldAt: "2026-11-22T12:00:00Z",
            receivedAt: "2026-11-22T12:00:05Z",
            outcomes: [
              {
                eventId: "ev-1",
                marketplace: "poshmark",
                action: "adjust_quantity",
                status: "succeeded",
                attempts: 1,
                error: null,
                listingId: "posh-1",
                newListingId: null,
                previousQuantity: 3,
                newQuantity: 2,
                nextRetryAt: null,
                updatedAt: "2026-11-22T12:00:10Z",
                retryable: false,
              },
              {
                eventId: "ev-2",
                marketplace: "depop",
                action: "adjust_quantity",
                status: "failed",
                attempts: 1,
                error: "Depop extension offline",
                listingId: "depop-1",
                newListingId: null,
                previousQuantity: 3,
                newQuantity: 2,
                nextRetryAt: "2026-11-22T12:05:10Z",
                updatedAt: "2026-11-22T12:00:10Z",
                retryable: true,
              },
            ],
          },
        ],
      },
    }));

    const html = renderToStaticMarkup(<SaleDetectionTracker />);
    expect(html).toContain("60-day sale detection tracker");
    expect(html).toContain("SKU SKU-WOOL-3");
    expect(html).toContain("3-Pack Wool Socks");
    expect(html).toContain("Depop extension offline");
    expect(html).toContain(">Retry<");
    expect(html).toContain("Qty 3 → 2");
  });
});
