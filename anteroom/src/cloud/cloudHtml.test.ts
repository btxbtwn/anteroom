import { expect, it } from "vitest";
import page from "../../cloud.html?raw";
import appIconUrl from "../../desktop/AppIcon.png";
import themeBoot from "../../public/assets/theme-boot.js?raw";

// The hosted API serves this page with `script-src 'self'`, which blocks inline scripts.
it("loads every script on the hosted page from a file", () => {
  const scripts = [...page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  expect(scripts.length).toBeGreaterThan(0);
  for (const [, attributes, body] of scripts) {
    expect(attributes).toMatch(/\ssrc="\/[^"]+"/);
    expect(body.trim()).toBe("");
  }
});

it("sets the theme before the stylesheet, so the first paint matches it", () => {
  // Served from /assets, which the hosted API answers without a session.
  const script = page.indexOf('<script src="/assets/theme-boot.js"></script>');
  expect(script).toBeGreaterThan(-1);
  expect(script).toBeLessThan(page.indexOf('rel="stylesheet"'));
  expect(page).toContain('<meta name="theme-color" content="#000000" />');
  expect(themeBoot).toContain('localStorage.getItem("anteroom:theme")');
  expect(themeBoot).toContain('meta[name="theme-color"]');
});

it("points the startup splash logo at an icon Vite bundles into /assets", () => {
  const match = page.match(/<img\s+class="startup-logo"\s+src="([^"]+)"/);
  expect(match?.[1]).toBe("/desktop/AppIcon.png");
  expect(appIconUrl).toContain("AppIcon.png");
});

it("publishes SEO, canonical, Open Graph, and Twitter metadata", () => {
  expect(page).toContain('<meta name="description" content="');
  expect(page).toContain('<link rel="canonical" href="https://myanteroom.com/" />');
  expect(page).toContain('<meta property="og:title" content="Anteroom — One listing. Every storefront." />');
  expect(page).toContain('<meta property="og:url" content="https://myanteroom.com/" />');
  expect(page).toContain('<meta name="twitter:card" content="summary" />');
});
