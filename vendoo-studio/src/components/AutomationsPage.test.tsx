import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Automation, AutomationApproval } from "../api/automations";
import { AutomationsPage } from "./AutomationsPage";
import { WorkspaceRuntimeProvider } from "../app/runtime";

const data: { automations: Automation[]; approvals: AutomationApproval[] } = { automations: [], approvals: [] };

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "ebay-offers"
      ? { connected: true, listings: [], history: [], lastCheck: null, lastError: null,
        allListings: { enabled: false, discountPercent: 10, minimumPrice: "" } }
      : queryKey[0] === "automations" ? data.automations
        : queryKey[0] === "automation-approvals" ? data.approvals
          : [],
    isLoading: false,
    error: null,
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const task = (overrides: Partial<Automation>): Automation => ({
  id: "a1",
  name: "Refresh stale listings",
  prompt: null,
  status: "active",
  timezone: "America/Chicago",
  schedule: { kind: "daily", time: "09:00" },
  filters: { min_age_days: 60 },
  actions: [{ type: "price", mode: "percent", value: 10, floor: 15, repeat: "step" }, { type: "relist" }],
  marketplaces: ["ebay", "poshmark"],
  summary: "",
  confirmed_at: "2026-11-01T00:00:00+00:00",
  next_run_at: "2026-11-05T15:00:00+00:00",
  last_run_at: "2026-11-04T15:00:00+00:00",
  created_at: null,
  updated_at: null,
  ...overrides,
});

const render = () => renderToStaticMarkup(<AutomationsPage onOpenListing={vi.fn()} />);

describe("AutomationsPage", () => {
  beforeEach(() => {
    data.automations = [];
    data.approvals = [];
  });

  it("opens on the list, with the form behind New automation", () => {
    const html = render();
    expect(html).toContain("Automations");
    expect(html).toContain("nothing lists or delists until you approve each item");
    expect(html).toContain("New automation");
    expect(html).toContain("No automations yet.");
    // The form lives in the side panel, closed until the seller asks for it.
    expect(html).not.toContain("Draft from prompt");
    expect(html).not.toContain("Save and confirm");
    // Nothing is waiting, so the approval section stays out of the way.
    expect(html).not.toContain("Waiting for approval");
    expect(html).not.toContain("Require approval before publishing");
    expect(html).toContain('aria-label="eBay offers"');
    expect(html).toContain("Automatic offers check every 15 minutes");
  });

  it("lists saved automations with their trigger, runs, a switch and a menu", () => {
    data.automations = [task({}), task({ id: "a2", name: "Weekend drop", status: "draft", next_run_at: null, last_run_at: null })];
    const html = render();
    expect(html).toContain("Refresh stale listings");
    expect(html).toContain("Every day 9:00 AM · listed 60+ days on eBay, Poshmark");
    expect(html).toContain("Nov 5, 9:00 AM");
    expect(html).toContain("Nov 4, 9:00 AM");
    expect(html).toContain('role="switch"');
    expect(html).toContain('aria-label="Pause Refresh stale listings"');
    expect(html).toContain('aria-label="More for Weekend drop"');
    // A draft is confirmed from its panel, not flipped on from the list.
    expect(html).toContain(">Draft<");
    expect(html).not.toContain("No automations yet.");
  });

  it("shows items waiting for approval, each approved on its own", () => {
    data.approvals = [{
      id: "p1", automation_id: "a1", run_id: "r1", conversation_id: "c1", title: "Blue tee",
      action: "relist", marketplaces: ["ebay"], state: "waiting", targets: {},
    } as unknown as AutomationApproval];
    const html = render();
    expect(html).toContain("Waiting for approval");
    expect(html).toContain("Blue tee");
    expect(html).toContain("Relist · eBay · waiting");
    expect(html).toContain("Review and approve");
  });

  it("on the web, shows eBay offers only while the offer timer runs", () => {
    const page = (features: string[]) => renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(features as never[]), views: new Set(["automations"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
        <AutomationsPage onOpenListing={vi.fn()} />
      </WorkspaceRuntimeProvider>,
    );
    const html = page(["inventory"]);
    expect(html).toContain("New automation");
    expect(html).not.toContain('aria-label="eBay offers"');
    expect(page(["inventory", "ebay_offers"])).toContain('aria-label="eBay offers"');
  });
});
