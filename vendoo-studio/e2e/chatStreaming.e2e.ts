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
