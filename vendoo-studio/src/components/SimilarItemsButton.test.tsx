import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { SimilarPair } from "../api/types";
import { SimilarPairList } from "./SimilarItemsButton";

const pair: SimilarPair = {
  first: { id: "a", title: "Marvel L Venom Tee", status: "active", brand: "Marvel", size: "L", price: 24, sku: "A19-4", cover_url: "/api/photos/p1/thumb?size=160" },
  second: { id: "b", title: "Marvel L Venom Tee Black", status: "sold", brand: null, size: null, price: null, sku: null, cover_url: null },
  reasons: ["similar_title", "similar_photo"],
  score: 0.94,
  photo_distance: 4,
  title_score: 0.94,
};

describe("SimilarPairList", () => {
  it("shows why two items look alike and offers keep, open and dismiss for each pair", () => {
    const html = renderToStaticMarkup(
      <SimilarPairList pairs={[pair]} busy={false} onOpen={vi.fn()} onKeep={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(html).toContain("Similar title");
    expect(html).toContain("Cover photos look alike");
    expect(html).toContain("Active · Marvel · Size L · $24 · SKU A19-4");
    expect(html).toContain("Sold");
    expect(html).toContain('src="/api/photos/p1/thumb?size=160"');
    expect(html.match(/Keep this one/g)).toHaveLength(2);
    expect(html).toContain("Not duplicates");
    expect(html).not.toContain("null");
  });

  it("disables every action while a merge or dismissal is saving", () => {
    const html = renderToStaticMarkup(
      <SimilarPairList pairs={[pair]} busy onOpen={vi.fn()} onKeep={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(html.match(/disabled=""/g)).toHaveLength(3);
  });
});
