import { describe, expect, it } from "vitest";
import { buttonVariants, InlineButton, type ButtonSize } from "./button";
import { renderToStaticMarkup } from "react-dom/server";

const COARSE_CENTERED_HIT_AREA = [
  "pointer-coarse:after:absolute",
  "pointer-coarse:after:top-1/2",
  "pointer-coarse:after:left-1/2",
  "pointer-coarse:after:-translate-x-1/2",
  "pointer-coarse:after:-translate-y-1/2",
  "pointer-coarse:after:size-full",
  "pointer-coarse:after:min-h-11",
  "pointer-coarse:after:min-w-11",
];

describe("buttonVariants coarse-pointer hit area", () => {
  const smallSizes: ButtonSize[] = ["xs", "icon-xs", "icon-micro", "icon-tiny", "micro", "compact"];

  it.each(smallSizes)("keeps compact visual size while extending centered 44px hit area for size=%s", (size) => {
    const classes = buttonVariants({ size }).split(/\s+/);
    for (const token of COARSE_CENTERED_HIT_AREA) {
      expect(classes).toContain(token);
    }
    // Visual size stays compact on fine pointers and dense layouts (never forces h-11/min-h-11).
    expect(classes).not.toContain("h-11");
    expect(classes).not.toContain("min-h-11");
  });

  it("keeps ::after explicitly centered even when a call site overrides flex alignment", () => {
    const classes = buttonVariants({ size: "xs", className: "justify-start items-start" }).split(/\s+/);
    expect(classes).toContain("pointer-coarse:after:top-1/2");
    expect(classes).toContain("pointer-coarse:after:left-1/2");
    expect(classes).toContain("pointer-coarse:after:-translate-x-1/2");
    expect(classes).toContain("pointer-coarse:after:-translate-y-1/2");
  });

  it("gives InlineButton a centered 44x44px coarse-pointer hit area", () => {
    const html = renderToStaticMarkup(<InlineButton className="text-xs">Show more</InlineButton>);
    const match = html.match(/class="([^"]+)"/);
    expect(match).not.toBeNull();
    const classes = match![1].split(/\s+/);
    for (const token of COARSE_CENTERED_HIT_AREA) {
      expect(classes).toContain(token);
    }
  });
});
