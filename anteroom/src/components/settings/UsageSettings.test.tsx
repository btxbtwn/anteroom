import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ManagedUsage } from "../../api/types";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { UsageSettings } from "./UsageSettings";

const service = { count: 0, usd: 0, input_tokens: 0, output_tokens: 0, items: [] };
const october: ManagedUsage = {
  period: { id: "30d", start: "2026-10-02T00:00:00+00:00", end: "2026-11-01T00:00:00+00:00" },
  months: ["2026-10", "2026-09"],
  total_usd: 0.3429,
  openrouter: {
    count: 3, usd: 0.0429, input_tokens: 2890, output_tokens: 710,
    items: [
      { label: "google/gemini-2.5-flash", count: 2, usd: 0.0425, input_tokens: 2800, output_tokens: 700 },
      { label: "openai/gpt-5-mini", count: 1, usd: 0.0004, input_tokens: 90, output_tokens: 10 },
    ],
  },
  photoroom: { ...service, count: 3, usd: 0.3, items: [{ label: "Background removed", count: 3, usd: 0.3, input_tokens: 0, output_tokens: 0 }] },
  days: Array.from({ length: 30 }, (_, index) => ({
    date: `2026-10-${String(index + 2).padStart(2, "0")}`,
    openrouter: index === 12 ? 0.0429 : 0,
    photoroom: index === 12 ? 0.1 : index === 29 ? 0.2 : 0,
  })),
};

function render(usage: ManagedUsage) {
  const client = new QueryClient();
  client.setQueryData(["managed-usage", undefined, "30d"], usage);
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><UsageSettings /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

it("shows the period's total, its dates, the costliest day and what each service went on", () => {
  const html = render(october);
  for (const text of [
    "Last 30 days", "Oct 2 – 31, 2026", "$0.34", "Highest day: Oct 31, $0.20",
    "3 AI calls · 3,600 tokens", "google/gemini-2.5-flash", "2 calls · 3,500 tokens", "$0.0004",
    "3 photo edits", "Background removed", "3 edits", "$0.30",
    "Days and months are in UTC.", "Download CSV",
  ]) expect(html).toContain(text);
  expect(html).not.toContain("No metered usage");
  // One bar per day, and a description for people who can't see them.
  expect(html.match(/group\/day/g)).toHaveLength(30);
  expect(html).toContain("Cost per day, Oct 2 to Oct 31. Highest day: Oct 31, $0.20. Table has every day.");
});

it("tells a new account what will show up here instead of drawing an empty chart", () => {
  const html = render({ ...october, total_usd: 0, openrouter: service, photoroom: service,
    days: october.days.map((day) => ({ ...day, openrouter: 0, photoroom: 0 })) });
  expect(html).toContain("No metered usage in this period.");
  expect(html).toContain("$0.00");
  expect(html).not.toContain("group/day");
  expect(html).not.toContain("OpenRouter");
  // Nothing to download yet.
  expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="Download CSV"|<button[^>]*aria-label="Download CSV"[^>]*disabled=""/);
});
