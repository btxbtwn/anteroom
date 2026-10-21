import { describe, expect, it } from "vitest";
import { installSoftwareKeyboard, keyboardState, KEYBOARD_CLOSED } from "./softwareKeyboard";

describe("keyboardState", () => {
  it("is closed when the visual viewport fills the window", () => {
    expect(keyboardState(852, { width: 393, height: 852, scale: 1 })).toEqual(KEYBOARD_CLOSED);
  });

  it("ignores the URL bar collapsing", () => {
    expect(keyboardState(852, { width: 393, height: 780, scale: 1 })).toEqual(KEYBOARD_CLOSED);
  });

  it("reports the height left above an iPhone keyboard", () => {
    expect(keyboardState(852, { width: 393, height: 516, scale: 1 })).toEqual({
      open: true,
      visibleHeight: 516,
      inset: 336,
    });
  });

  it("leaves pinch zoom alone", () => {
    expect(keyboardState(852, { width: 196, height: 426, scale: 2 })).toEqual(KEYBOARD_CLOSED);
  });

  it("is closed without a visual viewport", () => {
    expect(keyboardState(852, null)).toEqual(KEYBOARD_CLOSED);
  });
});

function fakeWindow({ coarse = true } = {}) {
  const listeners: Record<string, () => void> = {};
  const styles = new Map<string, string>();
  const viewport = {
    width: 393,
    height: 852,
    scale: 1,
    addEventListener: (type: string, fn: () => void) => {
      listeners[type] = fn;
    },
    removeEventListener: (type: string) => {
      delete listeners[type];
    },
    dispatchEvent: () => true,
  };
  const root = {
    dataset: {} as Record<string, string>,
    style: {
      setProperty: (name: string, value: string) => styles.set(name, value),
      removeProperty: (name: string) => styles.delete(name),
    },
  };
  const win = {
    innerHeight: 852,
    scrollY: 0,
    scrollTo(_x: number, y: number) {
      win.scrollY = y;
    },
    matchMedia: () => ({ matches: coarse }),
    visualViewport: viewport,
    document: { documentElement: root },
  };
  const resize = (height: number) => {
    viewport.height = height;
    listeners.resize?.();
  };
  return { win, root, styles, listeners, resize };
}

describe("installSoftwareKeyboard", () => {
  it("fits the app above the keyboard and undoes the iOS page slide", () => {
    const { win, root, styles, resize } = fakeWindow();
    installSoftwareKeyboard(win as never);
    win.scrollY = 280;
    resize(516);
    expect(root.dataset.keyboard).toBe("open");
    expect(styles.get("--app-height")).toBe("516px");
    expect(styles.get("--keyboard-inset")).toBe("336px");
    expect(win.scrollY).toBe(0);

    resize(852);
    expect(root.dataset.keyboard).toBeUndefined();
    expect(styles.has("--app-height")).toBe(false);
  });

  it("does nothing with a mouse", () => {
    const { win, listeners } = fakeWindow({ coarse: false });
    installSoftwareKeyboard(win as never);
    expect(Object.keys(listeners)).toEqual([]);
  });

  it("stops listening on cleanup", () => {
    const { win, listeners } = fakeWindow();
    const cleanup = installSoftwareKeyboard(win as never);
    expect(Object.keys(listeners).sort()).toEqual(["resize", "scroll"]);
    cleanup();
    expect(Object.keys(listeners)).toEqual([]);
  });
});
