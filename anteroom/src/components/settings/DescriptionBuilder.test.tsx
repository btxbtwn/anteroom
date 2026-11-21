// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DescriptionFormulaBlock } from "../../api/client";
import {
  DescriptionBuilder,
  descriptionPreview,
  insertBlockId,
  removeBlockId,
  reorderBlockIds,
} from "./DescriptionBuilder";

const BLOCKS: DescriptionFormulaBlock[] = [
  {
    id: "vibe",
    label: "Vibe",
    detail: "One short sentence of style and trend keywords.",
    snippet: "{trendy vibe/style keyword sentence with period}",
    default: true,
    enabled: true,
  },
  {
    id: "flaws",
    label: "Flaws",
    detail: "Each flaw on its own line.",
    snippet: "Flaws:\n- {one flaw per line}\nSee photos for details.",
    default: true,
    enabled: true,
  },
  {
    id: "measurements",
    label: "Measurements",
    detail: "Flat measurements, or See photos.",
    snippet: "Measurements: {See photos OR specific measurements}",
    default: true,
    enabled: true,
  },
  {
    id: "material",
    label: "Material",
    detail: "Fiber from the label, with blend percentages.",
    snippet: "Material: {fiber from the label}",
    default: false,
    enabled: false,
  },
  {
    id: "care",
    label: "Care",
    detail: "Care from the label.",
    snippet: "Care: {care from the label}",
    default: false,
    enabled: false,
  },
];

const EMPTY = "{one short factual description with no labeled sections}";

describe("DescriptionBuilder helpers", () => {
  it("renders preview in the exact chosen attribute order", () => {
    expect(descriptionPreview(BLOCKS, ["measurements", "material", "vibe"], EMPTY)).toBe(
      "Measurements: {See photos OR specific measurements}\n\nMaterial: {fiber from the label}\n\n{trendy vibe/style keyword sentence with period}",
    );
    expect(descriptionPreview(BLOCKS, [], EMPTY)).toBe(EMPTY);
  });

  it("reorders, inserts at exact positions, and removes without duplicates", () => {
    expect(reorderBlockIds(["vibe", "flaws", "measurements"], 2, 0)).toEqual([
      "measurements",
      "vibe",
      "flaws",
    ]);
    expect(reorderBlockIds(["vibe", "flaws"], -1, 1)).toEqual(["vibe", "flaws"]);
    expect(insertBlockId(["vibe", "flaws"], "material", 1)).toEqual([
      "vibe",
      "material",
      "flaws",
    ]);
    expect(insertBlockId(["vibe", "flaws"], "material")).toEqual(["vibe", "flaws", "material"]);
    expect(insertBlockId(["vibe", "flaws"], "flaws", 0)).toEqual(["flaws", "vibe"]);
    expect(removeBlockId(["vibe", "flaws", "measurements"], "flaws")).toEqual([
      "vibe",
      "measurements",
    ]);
  });
});

describe("DescriptionBuilder interactions", () => {
  const containers: HTMLElement[] = [];

  afterEach(() => {
    for (const container of containers.splice(0)) {
      container.remove();
    }
  });

  function renderBuilder(initialIds: string[]) {
    const container = document.createElement("div");
    document.body.appendChild(container);
    containers.push(container);
    const root = createRoot(container);
    const onChange = vi.fn();
    let currentIds = [...initialIds];

    const rerender = (ids: string[]) => {
      currentIds = ids;
      act(() => {
        root.render(
          <DescriptionBuilder
            blocks={BLOCKS}
            blockIds={currentIds}
            emptyDescription={EMPTY}
            onChange={(next) => {
              onChange(next);
              rerender(next);
            }}
          />,
        );
      });
    };

    rerender(initialIds);
    return {
      container,
      onChange,
      unmount: () => act(() => root.unmount()),
    };
  }

  it("adds, reorders, removes, and clears attributes with live preview and status updates", () => {
    const { container, onChange, unmount } = renderBuilder(["vibe", "flaws", "measurements"]);

    const activeIds = () =>
      Array.from(container.querySelectorAll('[data-slot="description-active-block"]')).map((el) =>
        el.getAttribute("data-block-id"),
      );
    const availableIds = () =>
      Array.from(container.querySelectorAll('[data-slot="description-available-block"]')).map(
        (el) => el.getAttribute("data-block-id"),
      );
    const preview = () =>
      container.querySelector('[data-slot="description-formula-preview"]')?.textContent ?? "";

    expect(activeIds()).toEqual(["vibe", "flaws", "measurements"]);
    expect(availableIds()).toEqual(["material", "care"]);

    // Add Material from Available attributes
    const addMaterial = container.querySelector<HTMLButtonElement>('button[aria-label="Add Material"]');
    expect(addMaterial).not.toBeNull();
    act(() => {
      addMaterial!.click();
    });
    expect(activeIds()).toEqual(["vibe", "flaws", "measurements", "material"]);
    expect(availableIds()).toEqual(["care"]);
    expect(preview()).toContain("Material: {fiber from the label}");

    // Move Measurements up above Flaws
    const moveUp = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Move Measurements up"]',
    );
    expect(moveUp).not.toBeNull();
    act(() => {
      moveUp!.click();
    });
    expect(activeIds()).toEqual(["vibe", "measurements", "flaws", "material"]);
    expect(preview().indexOf("Measurements:")).toBeLessThan(preview().indexOf("Flaws:"));

    // Remove Flaws
    const removeFlaws = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove Flaws"]',
    );
    expect(removeFlaws).not.toBeNull();
    act(() => {
      removeFlaws!.click();
    });
    expect(activeIds()).toEqual(["vibe", "measurements", "material"]);
    expect(availableIds()).toContain("flaws");
    expect(preview()).not.toContain("Flaws:");

    // Clear all shows empty drop zone and fallback sentence preview
    const clearButton = Array.from(container.querySelectorAll("button")).find(
      (btn) => btn.textContent?.trim() === "Clear",
    );
    expect(clearButton).not.toBeNull();
    act(() => {
      clearButton!.click();
    });
    expect(activeIds()).toEqual([]);
    expect(preview()).toBe(EMPTY);
    expect(container.textContent).toContain("Drag attributes here");
    expect(onChange).toHaveBeenCalledTimes(4);

    unmount();
  });
});
