import { expect, it } from "vitest";
import page from "../../cloud.html?raw";
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
  expect(themeBoot).toContain('localStorage.getItem("vendoo-studio:theme")');
});
