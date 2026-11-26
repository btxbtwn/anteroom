import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MarkSoldForm } from "./MarkSoldForm";

const form = (initial: Parameters<typeof MarkSoldForm>[0]["initial"]) => renderToStaticMarkup(
  <MarkSoldForm initial={initial} pending={false} onSubmit={vi.fn()} onCancel={vi.fn()} />);
const save = (html: string) => html.match(/<button[^>]*type="submit"[^>]*>/)?.[0] ?? "";

describe("Mark as sold form", () => {
  it("opens from the listing's menu with nothing picked, and Save waits for Where it sold", () => {
    const html = form(null);
    expect(html).toContain("Choose…");
    expect(html).not.toContain(">Other<");
    expect(save(html)).toContain('disabled=""');
  });

  it("opens from a marketplace's menu with that marketplace picked", () => {
    const html = form("ebay");
    expect(html).toContain("eBay");
    expect(save(html)).not.toContain('disabled=""');
  });
});
