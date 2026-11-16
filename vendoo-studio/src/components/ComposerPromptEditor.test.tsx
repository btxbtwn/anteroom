// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { ComposerPromptEditor } from "./ComposerPromptEditor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.replaceChildren();
});

const settle = () => act(() => new Promise<void>(resolve => setTimeout(resolve, 10)));
const editor = (autoFocus: boolean) => <ComposerPromptEditor value="" onChange={() => {}} onSubmit={() => {}} placeholder="Ask" handleRef={{ current: null }} autoFocus={autoFocus}/>;

it("takes focus when it opens and nothing else has it", async () => {
  act(() => root.render(editor(true)));
  await settle();
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Message");
});

it("leaves focus where the seller put it before the composer opened", async () => {
  // The seller opened the Account menu while the Assistant was still loading
  // (2026-11-15 audit): a late autofocus closed the menu under their click.
  const menu = document.createElement("button");
  menu.textContent = "Account";
  document.body.append(menu);
  menu.focus();
  act(() => root.render(editor(true)));
  await settle();
  expect(document.activeElement).toBe(menu);
});
