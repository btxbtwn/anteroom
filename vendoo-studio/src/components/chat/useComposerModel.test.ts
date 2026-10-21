import { describe, expect, it } from "vitest";
import { cursorPickerModels, type ModelOption } from "./useComposerModel";

function options(ids: string[]): ModelOption[] {
  return ids.map((value) => ({ value, label: value === "auto" ? "Auto" : value }));
}

describe("cursorPickerModels", () => {
  it("keeps one current model from each family and the model already selected", () => {
    const models = options([
      "auto",
      "composer-2",
      "composer-2.5",
      "composer-2.5-fast",
      "grok-4",
      "grok-4.7",
      "claude-sonnet-4-6",
      "claude-sonnet-5",
      "claude-opus-4-6",
      "claude-opus-4-7",
      "claude-haiku-4-5",
      "gpt-5.3-codex",
      "gpt-5.4",
      "gpt-5.3",
    ]);

    expect(cursorPickerModels(models, "grok-4.7").map((model) => model.value)).toEqual([
      "auto",
      "composer-2.5",
      "grok-4.7",
      "claude-sonnet-5",
      "claude-opus-4-7",
      "gpt-5.4",
    ]);
    expect(cursorPickerModels(models, "gpt-5.3-codex").map((model) => model.value)).toContain("gpt-5.3-codex");
  });
});
