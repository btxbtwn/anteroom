// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { ComposerPrimaryActions } from "./ComposerPrimaryActions";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function mount(props: Partial<Parameters<typeof ComposerPrimaryActions>[0]>) {
  const host = document.createElement("div");
  const root = createRoot(host);
  const onSend = vi.fn();
  act(() => root.render(<ComposerPrimaryActions busy canSubmit onSend={onSend} onStop={() => {}} {...props} />));
  const button = (label: string) => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  return { onSend, button, unmount: () => act(() => root.unmount()) };
}

describe("composer follow-up buttons", () => {
  it("steers on a click and queues on a modifier-click while the reply can take a steer", () => {
    const { onSend, button, unmount } = mount({ canSteer: true });
    act(() => button("Steer message")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    act(() => button("Steer message")!.dispatchEvent(new MouseEvent("click", { bubbles: true, metaKey: true })));
    expect(onSend.mock.calls).toEqual([[false], [true]]);
    unmount();
  });

  it("gives a touch screen, which has no modifier key, its own queue button", () => {
    const { onSend, button, unmount } = mount({ canSteer: true });
    act(() => button("Queue message")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onSend).toHaveBeenCalledWith(true);
    unmount();
  });

  it("only queues when the run cannot take a steer", () => {
    const { button, unmount } = mount({ canSteer: false });
    expect(button("Steer message")).toBeNull();
    expect(button("Queue message")).not.toBeNull();
    unmount();
  });
});
