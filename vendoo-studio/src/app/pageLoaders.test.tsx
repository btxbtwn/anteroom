// @vitest-environment happy-dom
import { act, lazy, Suspense, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { preloadablePage } from "./pageLoaders";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Sourcing({ name }: { name: string }) {
  return <p>{name} boxes</p>;
}
const loadSourcing = () => Promise.resolve({ default: Sourcing });

let container: HTMLDivElement;
let fallbacks: number;
/** Counts each time the fallback reaches the screen. */
function Spinner() {
  useEffect(() => { fallbacks += 1; });
  return <p>Loading</p>;
}
beforeEach(() => {
  fallbacks = 0;
  container = document.createElement("div");
  document.body.append(container);
});
afterEach(() => container.remove());

async function open(Page: React.ComponentType<{ name: string }>) {
  const root = createRoot(container);
  await act(async () => root.render(<Suspense fallback={<Spinner />}><Page name="Wholesale" /></Suspense>));
  return root;
}

describe("preloaded pages", () => {
  it("React.lazy shows its fallback even when the code is already loaded", async () => {
    await loadSourcing();
    const root = await open(lazy(loadSourcing));
    expect(container.textContent).toBe("Wholesale boxes");
    expect(fallbacks).toBeGreaterThan(0);
    root.unmount();
  });

  it("renders a preloaded page straight away", async () => {
    const page = preloadablePage(loadSourcing);
    await page.preload();
    const root = await open(page.Page);
    expect(container.textContent).toBe("Wholesale boxes");
    expect(fallbacks).toBe(0);
    root.unmount();
  });

  it("still loads a page opened before its code arrived", async () => {
    const page = preloadablePage(loadSourcing);
    const root = await open(page.Page);
    expect(container.textContent).toBe("Wholesale boxes");
    root.unmount();
  });

  it("asks again after a failed load", async () => {
    let calls = 0;
    const page = preloadablePage(() => (++calls === 1 ? Promise.reject(new Error("offline")) : loadSourcing()));
    await expect(page.preload()).rejects.toThrow("offline");
    await page.preload();
    expect(calls).toBe(2);
  });
});
