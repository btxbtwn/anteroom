import { describe, expect, it } from "vitest";
import { KEYBOARD_REST, typedEvents } from "./browserKeyboard";

const backspace = [
  { kind: "key", type: "down", key: "Backspace", modifiers: 0 },
  { kind: "key", type: "up", key: "Backspace", modifiers: 0 },
];
const enter = [
  { kind: "key", type: "down", key: "Enter", modifiers: 0 },
  { kind: "key", type: "up", key: "Enter", modifiers: 0 },
];

describe("typedEvents", () => {
  it("sends typed text", () => {
    expect(typedEvents(KEYBOARD_REST, `${KEYBOARD_REST}h`)).toEqual([{ kind: "text", text: "h" }]);
    expect(typedEvents(`${KEYBOARD_REST}h`, `${KEYBOARD_REST}hey`)).toEqual([{ kind: "text", text: "ey" }]);
  });

  it("sends nothing when the box did not change", () => {
    expect(typedEvents(KEYBOARD_REST, KEYBOARD_REST)).toEqual([]);
  });

  it("turns Backspace on the resting box into a Backspace on the draft", () => {
    expect(typedEvents(KEYBOARD_REST, "")).toEqual(backspace);
  });

  it("replays an autocorrected word as deletes and the new ending", () => {
    expect(typedEvents(`${KEYBOARD_REST}teh`, `${KEYBOARD_REST}the `)).toEqual([
      ...backspace,
      ...backspace,
      { kind: "text", text: "he " },
    ]);
  });

  it("presses Enter for a new line", () => {
    expect(typedEvents(KEYBOARD_REST, `${KEYBOARD_REST}\n`)).toEqual(enter);
    expect(typedEvents(KEYBOARD_REST, `${KEYBOARD_REST}a\nb`)).toEqual([
      { kind: "text", text: "a" },
      ...enter,
      { kind: "text", text: "b" },
    ]);
  });

  it("deletes an emoji with one Backspace and never splits one", () => {
    expect(typedEvents(`${KEYBOARD_REST}😀`, KEYBOARD_REST)).toEqual(backspace);
    expect(typedEvents(`${KEYBOARD_REST}😀`, `${KEYBOARD_REST}😁`)).toEqual([...backspace, { kind: "text", text: "😁" }]);
  });
});
