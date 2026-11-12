import { test, expect } from "@playwright/test";

// The Mac app's window injects `window.pywebview`; this stands in for its
// ChatGPT sign-in calls, which the real app runs on the Mac's own server.
const macBridge = () => {
  let pending = false;
  const answer = (value: object) => Promise.resolve({ ok: true, ...value });
  Object.assign(window, {
    pywebview: {
      api: {
        connect_chatgpt_worker: () => answer({}),
        chatgpt_sign_in: () => {
          pending = true;
          return answer({});
        },
        chatgpt_sign_in_status: () => answer({ signed_in: false, pending, error: null }),
        chatgpt_sign_in_cancel: () => {
          pending = false;
          return answer({});
        },
      },
    },
  });
};

test("the Mac app's account window signs this Mac into ChatGPT", async ({ page }, info) => {
  await page.addInitScript(macBridge);
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const phone = info.project.name === "phone";
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Providers", exact: true }).click();
  await page.getByRole("button", { name: "Select ChatGPT", exact: true }).click();

  const row = page.locator('[data-slot="settings-row"]', { hasText: "Sign in with ChatGPT" });
  await expect(page.getByText("the sign-in stays on this computer")).toBeVisible();
  await expect(page.getByText("Open Anteroom on your computer")).toHaveCount(0);
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/mac-chatgpt-sign-in-${info.project.name}.png` });

  await row.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Approve Anteroom in your browser, and leave token sharing on.", { exact: true })).toBeVisible();
  await page.screenshot({ path: `e2e/results/hosted/mac-chatgpt-sign-in-pending-${info.project.name}.png` });

  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(row.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
});
