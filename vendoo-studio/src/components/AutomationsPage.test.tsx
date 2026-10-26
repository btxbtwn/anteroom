import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AutomationsPage } from "./AutomationsPage";

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: [], isLoading: false, error: null }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

describe("AutomationsPage", () => {
  it("asks for a sentence and does not run anything until it is confirmed", () => {
    const html = renderToStaticMarkup(<AutomationsPage />);
    expect(html).toContain("Automations");
    expect(html).toContain("Nothing runs until you confirm");
    expect(html).toContain("Draft from prompt");
    expect(html).toContain("Preview");
    expect(html).toContain("Save and confirm");
    expect(html).toContain("60 days listed on eBay and Poshmark");
    expect(html).toContain("No automations yet.");
  });
});
