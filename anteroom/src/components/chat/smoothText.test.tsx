// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { revealStep, useSmoothText } from "./smoothText";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("revealStep", () => {
  it("moves at least one character while text is waiting", () => {
    expect(revealStep(10, 11, 1)).toBe(11);
    expect(revealStep(0, 5, 0)).toBe(1);
  });

  it("moves faster the more text is waiting, and never past the end", () => {
    const small = revealStep(0, 40, 32);
    const large = revealStep(0, 4000, 32);
    expect(small).toBeGreaterThan(0);
    expect(small).toBeLessThan(40);
    expect(large).toBeGreaterThan(small * 10);
    expect(large).toBeLessThan(4000);
    expect(revealStep(0, 40, 60_000)).toBe(40);
  });

  it("catches up with a large chunk in under a second", () => {
    let shown = 0;
    let elapsed = 0;
    while (shown < 1200 * 0.95) {
      shown = revealStep(shown, 1200, 32);
      elapsed += 32;
    }
    expect(elapsed).toBeGreaterThan(200);
    expect(elapsed).toBeLessThan(1000);
  });

  it("falls back to the text's length when the text got shorter", () => {
    expect(revealStep(50, 20, 32)).toBe(20);
  });
});

describe("useSmoothText", () => {
  let root: Root;
  let host: HTMLElement;
  let reduced = false;
  const seen: string[] = [];

  function Probe({ text }: { text: string }) {
    const shown = useSmoothText(text);
    seen.push(shown);
    return <p>{shown}</p>;
  }

  const render = (text: string) => act(() => root.render(<Probe text={text} />));
  // One frame an act: each step schedules the next from an effect.
  const advance = (ms: number) => {
    for (let passed = 0; passed <= ms; passed += 40) act(() => { vi.advanceTimersByTime(40); });
  };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "setTimeout", "clearTimeout"] });
    reduced = false;
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      addEventListener() {},
      removeEventListener() {},
    }));
    seen.length = 0;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows what is already there at once, so a reopened answer is not typed out again", () => {
    render("An answer that was half written when the page opened.");
    expect(host.textContent).toBe("An answer that was half written when the page opened.");
  });

  it("reveals a large chunk progressively instead of all at once", () => {
    render("Start.");
    const chunk = " word".repeat(200);
    render(`Start.${chunk}`);
    expect(host.textContent).toBe("Start.");
    advance(100);
    const partial = host.textContent ?? "";
    expect(partial.length).toBeGreaterThan("Start.".length);
    expect(partial.length).toBeLessThan(`Start.${chunk}`.length);
    expect(`Start.${chunk}`.startsWith(partial)).toBe(true);
    advance(2000);
    expect(host.textContent).toBe(`Start.${chunk}`);
    // Every step shows more than the one before.
    const lengths = seen.map((text) => text.length);
    expect(lengths).toEqual([...lengths].sort((a, b) => a - b));
  });

  it("keeps revealing when text arrives faster than it steps", () => {
    render("Start.");
    let text = "Start.";
    // A new piece every 10ms for a second: quicker than one reveal step.
    for (let piece = 0; piece < 100; piece++) {
      text += " word";
      render(text);
      act(() => { vi.advanceTimersByTime(10); });
    }
    const shown = (host.textContent ?? "").length;
    expect(shown).toBeGreaterThan(text.length * 0.6);
  });

  it("does not jump ahead after a pause between chunks", () => {
    render("Start.");
    render(`Start.${" word".repeat(40)}`);
    advance(3000);
    expect(host.textContent).toBe(`Start.${" word".repeat(40)}`);
    // Nothing arrives for a while; the next chunk is still revealed gradually.
    act(() => { vi.advanceTimersByTime(5000); });
    const next = `Start.${" word".repeat(240)}`;
    render(next);
    act(() => { vi.advanceTimersByTime(45); });
    expect((host.textContent ?? "").length).toBeLessThan(next.length * 0.6);
  });

  it("shows text as it arrives when the seller asked for reduced motion", () => {
    reduced = true;
    render("Start.");
    render(`Start.${" word".repeat(200)}`);
    advance(0);
    expect(host.textContent).toBe(`Start.${" word".repeat(200)}`);
  });

  it("starts over when the answer is replaced by a shorter one", () => {
    render("A long first answer that is replaced.");
    render("New");
    expect(host.textContent).toBe("New");
  });
});
