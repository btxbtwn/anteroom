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
  // The follow-up gets its own full answer.
  // There can be a moment with no live row between the interrupted answer and its successor.
  await expect(answers).toHaveCount(2, { timeout: 30_000 });
  await expect(streaming).toHaveCount(0);
  await expect(answers.last()).toContainText("Sandbox Levi's M Blue Cotton Crew Neck T-Shirt");
});

/**
 * Answers that have run to their last word. The queued question's answer can
 * start a moment after the queue empties, with nothing streaming in between,
 * and the sandbox answers one question at a time: a test that ends in that
 * moment leaves the answer running and the next test's question is refused.
 * The slow answer ends with "The end."; the follow-up has no slow marker, so
 * the sandbox sends its whole reply at once.
 */
function finishedAnswers(page: import("@playwright/test").Page) {
  return page.locator('[data-message-role="assistant"]:not([aria-busy="true"])', {
    hasText: /The end\.|Sandbox Levi's M Blue Cotton Crew Neck T-Shirt/,
  });
}

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
  await expect(finishedAnswers(page)).toHaveCount(2, { timeout: 45_000 });
  await expect(streaming).toHaveCount(0);
});

/** A queued follow-up survives a reload and still goes once the answer it waited for ends. */
test("a queued follow-up is still sent after the page reloads", async ({ page, phone }) => {
  const composer = await openAssistant(page, phone);
  // Its own title, so Recent chats opens this conversation and not an earlier test's.
  const title = `Reload check ${Date.now()}`;
  await composer.fill(`${title} [sandbox:slow]`);
  await composer.press("Enter");
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox is streaming", { timeout: 15_000 });
  await composer.fill("And the week before?");
  await composer.press("ControlOrMeta+Enter");
  await expect(page.locator("[data-queued-message-id]")).toHaveCount(1);

  await page.reload();
  // Reopen Assistant from Today, then go back to the conversation.
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Assistant", exact: true }).click();
  if (phone) {
    await page.getByRole("button", { name: "Show chat history" }).click();
    await page.getByRole("button").filter({ hasText: title }).first().click();
  } else {
    await page.getByRole("region", { name: "Recent chats" }).getByRole("button")
      .filter({ hasText: title }).first().click();
  }
  const queued = page.locator("[data-queued-message-id]");
  // It waits for the answer still being written, then goes by itself.
  await expect(queued).toHaveCount(0, { timeout: 45_000 });
  await expect(page.locator('[data-message-role="user"]').last()).toContainText("And the week before?");
  await expect(finishedAnswers(page)).toHaveCount(2, { timeout: 45_000 });
  await expect(streaming).toHaveCount(0);
  await expect(page.locator('[data-message-role="assistant"]')).toHaveCount(2);
});

/** How many different lengths the answer being written shows over `ms`, read once a frame. */
function countRevealSteps(page: import("@playwright/test").Page, ms: number) {
  return page.evaluate((duration) => new Promise<number>((resolve) => {
    const lengths = new Set<number>();
    const started = performance.now();
    const read = () => {
      const row = document.querySelector('[data-message-role="assistant"][aria-busy="true"]');
      if (row) lengths.add((row.textContent ?? "").length);
      if (performance.now() - started < duration) requestAnimationFrame(read);
      else resolve(lengths.size);
    };
    read();
  }), ms);
}

/** A reply that arrives a few large chunks at a time is shown flowing, each new word fading in. */
test("a reply delivered in large chunks is revealed word by word", async ({ page, phone }) => {
  const composer = await openAssistant(page, phone);
  await composer.fill("Tell me something long [sandbox:burst]");
  await composer.press("Enter");
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox sends this reply", { timeout: 15_000 });

  const word = streaming.locator(".chat-stream-word").first();
  await expect(word).toBeVisible();
  expect(await word.evaluate((el) => getComputedStyle(el).animationName)).toBe("chat-word-in");
  // Six chunks arrive in all, two or three of them in this time; shown as they
  // land, the answer would take a handful of lengths at most.
  expect(await countRevealSteps(page, 1500)).toBeGreaterThan(10);

  // Nothing is held back once the reply ends, and the saved answer is plain text again.
  const answer = page.locator('[data-message-role="assistant"]:not([aria-busy="true"])', { hasText: "The end." });
  await expect(answer).toHaveCount(1, { timeout: 30_000 });
  await expect(streaming).toHaveCount(0);
  await expect(answer.locator(".chat-stream-word")).toHaveCount(0);
});

/** Reduced motion: text appears as it arrives, with no pacing and no fade. */
test("with reduced motion a reply shows as it arrives", async ({ page, phone }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const composer = await openAssistant(page, phone);
  await composer.fill("Tell me something long [sandbox:burst]");
  await composer.press("Enter");
  const streaming = page.locator('[data-message-role="assistant"][aria-busy="true"]');
  await expect(streaming).toContainText("Sandbox sends this reply", { timeout: 15_000 });

  const word = streaming.locator(".chat-stream-word").first();
  expect(await word.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  expect(await countRevealSteps(page, 1500)).toBeLessThanOrEqual(5);

  const answer = page.locator('[data-message-role="assistant"]:not([aria-busy="true"])', { hasText: "The end." });
  await expect(answer).toHaveCount(1, { timeout: 30_000 });
});

/** The messages and the message box share their edges; a phone's box rests at one line and reads at 16px. */
test("the conversation lines up with the message box, and a phone gets a one-line box", async ({ page, phone }) => {
  const composer = await openAssistant(page, phone);
  await composer.fill("Line me up");
  await composer.press("Enter");
  const answer = page.locator('[data-message-role="assistant"]:not([aria-busy="true"])');
  await expect(answer).toHaveCount(1, { timeout: 30_000 });

  const edges = async (selector: string) => {
    const box = await page.locator(selector).last().boundingBox();
    return { left: Math.round(box!.x), right: Math.round(box!.x + box!.width) };
  };
  expect(await edges('[data-message-role="user"]')).toEqual(await edges('[data-slot="composer-host"]'));
  // Still lined up once the conversation is long enough to scroll and a scrollbar takes its room.
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: viewport.width, height: 420 });
  const scroller = page.locator(".chat-scroll");
  await expect.poll(() => scroller.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  expect(await edges('[data-message-role="user"]')).toEqual(await edges('[data-slot="composer-host"]'));
  await page.setViewportSize(viewport);

  if (!phone) return;
  const prose = page.locator('[data-message-role="assistant"] .chat-markdown').last();
  expect(await prose.evaluate((el) => getComputedStyle(el).fontSize)).toBe("16px");
  // At rest the box is one touch target tall; it grows with what the seller types.
  const resting = (await composer.boundingBox())!.height;
  expect(resting).toBeLessThanOrEqual(52);
  await composer.fill("A longer note to the assistant ".repeat(8));
  expect((await composer.boundingBox())!.height).toBeGreaterThan(resting + 40);
  await composer.fill("");
});
