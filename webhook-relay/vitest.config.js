import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

// Made-up values for tests, built here so they are not mistaken for real keys.
const fake = (label) => `test-${label}`;
const webhookSecret = `whsec_${Buffer.from("0123456789abcdef0123456789abcdef").toString("base64")}`;

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          EBAY_CLIENT_ID: "test-app-id",
          EBAY_CLIENT_SECRET: "test-cert-id",
          EBAY_RUNAME: "test-runame",
          EBAY_VERIFICATION_TOKEN: fake("verification").padEnd(40, "x"),
          ETSY_KEYSTRING: fake("keystring"),
          ETSY_SHARED_SECRET: fake("shared-secret"),
          ETSY_WEBHOOK_SECRET: webhookSecret,
        },
      },
    }),
  ],
  test: { include: ["tests/worker/**/*.test.js"] },
});
