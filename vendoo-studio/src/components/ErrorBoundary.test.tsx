// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let broken = true;
function Listing() {
  if (broken) throw new Error("bad data");
  return <p>Listing form</p>;
}

let container: HTMLDivElement;
beforeEach(() => {
  broken = true;
  container = document.createElement("div");
  document.body.append(container);
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  container.remove();
  vi.restoreAllMocks();
});

describe("ErrorBoundary", () => {
  it("keeps a failed pane from blanking the app and recovers on Try again", async () => {
    const root = createRoot(container);
    await act(async () => root.render(<div><nav>Sidebar</nav><ErrorBoundary label="This listing"><Listing /></ErrorBoundary></div>));
    expect(container.textContent).toContain("Sidebar");
    expect(container.querySelector("[role=alert]")?.textContent).toContain("This listing couldn’t be shown");
    broken = false;
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "Try again")!;
    await act(async () => retry.click());
    expect(container.textContent).toContain("Listing form");
    await act(async () => root.unmount());
  });

  it("clears the error when another listing opens", async () => {
    const root = createRoot(container);
    await act(async () => root.render(<ErrorBoundary label="This listing" resetKey="a"><Listing /></ErrorBoundary>));
    expect(container.querySelector("[role=alert]")).not.toBeNull();
    broken = false;
    await act(async () => root.render(<ErrorBoundary label="This listing" resetKey="b"><Listing /></ErrorBoundary>));
    expect(container.textContent).toBe("Listing form");
    await act(async () => root.unmount());
  });

  it("offers only Reload when the screen's code could not be downloaded", async () => {
    function Missing(): never {
      throw new TypeError("Failed to fetch dynamically imported module: /assets/Settings.js");
    }
    const root = createRoot(container);
    await act(async () => root.render(<ErrorBoundary label="This page"><Missing /></ErrorBoundary>));
    expect(container.textContent).toContain("Anteroom was updated");
    expect([...container.querySelectorAll("button")].map((button) => button.textContent)).toEqual(["Reload"]);
    await act(async () => root.unmount());
  });
});
