import { expect, test } from "./fixtures";

/** A streaming reply shows its words as they arrive, Esc stops it, and what it had written stays. */
test("an assistant reply streams word by word, and Esc stops it keeping the partial answer", async ({ page, phone }) => {
  expect((await page.request.post("/api/assistant/threads")).ok()).toBe(true);
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Assistant", exact: true }).click();

  const composer = page.getByRole("textbox").last();
  await composer.click();
  await composer.fill("Tell me something long [sandbox:slow]");
  await composer.press("Enter");

  // The paragraph being written shows before it is finished.
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox is streaming", { timeout: 15_000 });
  await expect(streaming.locator("strong")).toHaveText("Sandbox");
  await expect(streaming).not.toContainText("The end.");

  await composer.click();
  await page.keyboard.press("Escape");

  await expect(streaming).toHaveCount(0, { timeout: 15_000 });
  const answers = page.locator('[data-message-role="assistant"]');
  await expect(answers.last()).toContainText("Sandbox is streaming");
  await expect(answers.last()).not.toContainText("The end.");
  await expect(page.getByRole("status").filter({ hasText: "Answer finished." })).toHaveCount(1);
});

async function openAssistant(page: import("@playwright/test").Page, phone: boolean) {
  expect((await page.request.post("/api/assistant/threads")).ok()).toBe(true);
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Assistant", exact: true }).click();
  const composer = page.getByRole("textbox").last();
  await composer.click();
  return composer;
}

/** As in T3 Code: Enter mid-answer steers, cutting the answer off and sending the follow-up next. */
test("a follow-up sent while an answer streams cuts it off and goes next", async ({ page, phone }) => {
  const composer = await openAssistant(page, phone);
  await composer.fill("Tell me something long [sandbox:slow]");
  await composer.press("Enter");
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox is streaming", { timeout: 15_000 });

  await composer.fill("Actually, just the totals");
  await composer.press("Enter");

  const users = page.locator('[data-message-role="user"]');
  await expect(users.last()).toContainText("Actually, just the totals", { timeout: 15_000 });
  await expect(page.locator("[data-queued-message-id]")).toHaveCount(0);
  // The cut-off answer keeps what it had written, and never finished.
  const answers = page.locator('[data-message-role="assistant"]:not([aria-busy="true"])');
  await expect(answers.first()).toContainText("Sandbox is streaming");
  await expect(answers.first()).not.toContainText("The end.");
  // The follow-up gets its own full answer (the sandbox streams it slowly too).
  await expect(streaming).toHaveCount(0, { timeout: 30_000 });
  await expect(answers).toHaveCount(2);
  await expect(answers.last()).toContainText("The end.");
});

/** Mod+Enter queues instead; a queued follow-up can be edited, then sends when the answer ends. */
test("a queued follow-up can be edited and sends once the answer finishes", async ({ page, phone }) => {
  const composer = await openAssistant(page, phone);
  await composer.fill("Tell me something long [sandbox:slow]");
  await composer.press("Enter");
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox is streaming", { timeout: 15_000 });

  await composer.fill("And last week?");
  await composer.press("ControlOrMeta+Enter");
  const queued = page.locator("[data-queued-message-id]");
  await expect(queued).toHaveCount(1);
  await expect(queued).toContainText("And last week?");
  await expect(queued.getByRole("button", { name: "Send now" })).toBeVisible();

  await queued.getByRole("button", { name: "Edit queued message" }).click();
  await expect(queued).toContainText("Editing in the message box");
  await composer.fill("And the week before?");
  await composer.press("Enter");
  await expect(queued).toContainText("And the week before?");
  await expect(queued).toContainText("Queued");

  // The first answer runs to its end, then the queued question goes.
  // (A queued bubble is a user row too, so wait for the queue to empty first.)
  await expect(queued).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('[data-message-role="user"]').last()).toContainText("And the week before?");
  await expect(page.locator('[data-message-role="assistant"]').first()).toContainText("The end.");
  await expect(streaming).toHaveCount(0, { timeout: 30_000 });
});
