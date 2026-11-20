import { describe, expect, it } from "vitest";

interface FsPromises {
  readFile(path: URL, encoding: "utf-8"): Promise<string>;
}

async function readCss(relativePath: string): Promise<string> {
  const mod = `node:${"fs/promises"}`;
  const { readFile } = (await import(/* @vite-ignore */ mod)) as FsPromises;
  return readFile(new URL(relativePath, import.meta.url), "utf-8");
}

describe("UI audit CSS regressions", () => {
  it("renders checked native checkbox checkmarks with var(--primary-foreground) instead of hardcoded white", async () => {
    const controlsCss = await readCss("./controls.css");
    expect(controlsCss).not.toContain("stroke='white'");
    expect(controlsCss).toMatch(/input\[type="checkbox"\]:not\(\[data-slot\]\):checked::after[\s\S]*background-color:\s*var\(--primary-foreground\)/);
  });

  it("keeps mobile confirm dialog footer buttons at a 44px minimum touch target", async () => {
    const overlaysCss = await readCss("./overlays.css");
    const mobileBlock = overlaysCss.slice(overlaysCss.indexOf("@media (max-width: 900px)"));
    expect(mobileBlock).toMatch(/\.confirm-dialog-footer\s+\.btn\s*\{[\s\S]*?min-height:\s*44px/);
    expect(mobileBlock).not.toMatch(/\.confirm-dialog-footer\s+\.btn\s*\{[\s\S]*?min-height:\s*32px/);
  });

  it("scrolls hosted auth pages vertically and colors auth alerts with destructive foreground", async () => {
    const workspaceCss = await readCss("../cloud/workspace.css");
    expect(workspaceCss).toMatch(/\.cloud-auth\{[^}]*height:100%[^}]*overflow-y:auto/);
    expect(workspaceCss).toContain('.cloud-auth p[role="alert"]{color:var(--destructive-foreground)}');
  });
});
