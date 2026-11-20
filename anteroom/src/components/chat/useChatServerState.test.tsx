import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { useChatServerState } from "./useChatServerState";

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({}),
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "activity" ? { busy: true } : undefined,
    isLoading: false,
  }),
}));
vi.mock("../listingSession", () => ({ useListingSessionActive: () => true }));

function Status({ localBusy }: { localBusy: boolean }) {
  const { busy } = useChatServerState("listing", localBusy);
  return <span>{String(busy)}</span>;
}

describe("chat activity", () => {
  it("does not show listing operations as a chat request", () => {
    expect(renderToStaticMarkup(<Status localBusy={false} />)).toBe("<span>false</span>");
  });
  it("shows an explicit chat request as busy", () => {
    expect(renderToStaticMarkup(<Status localBusy />)).toBe("<span>true</span>");
  });
});
