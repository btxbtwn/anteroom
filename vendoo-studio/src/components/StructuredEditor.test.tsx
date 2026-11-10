import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceFormField } from "../api/types";
import { StructuredEditor } from "./ListingEditor";

const { state, choices, inventories } = vi.hoisted(() => ({
  state: { fields: [] as MarketplaceFormField[] },
  choices: vi.fn(),
  inventories: vi.fn(),
}));
vi.mock("@tanstack/react-query", async original => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "native-editor-form"
      ? { marketplace: "mercari", native: true, fields: state.fields }
      : undefined,
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ setQueryData: vi.fn() }),
}));
vi.mock("./ListingFormControls", async (original) => ({
  ...await original<typeof import("./ListingFormControls")>(),
  SearchableSelect: (props: unknown) => { choices(props); return null; },
}));
vi.mock("./NativeInventoryEditor", () => ({
  NativeInventoryEditor: (props: unknown) => { inventories(props); return null; },
}));

function field(path: string, extra: Partial<MarketplaceFormField> = {}): MarketplaceFormField {
  return { key: path, path, label: path, value: "", options: [], required: false, multi: false,
    selection_only: false, section: "Details", ...extra };
}

function render(disabled = false, onChange = vi.fn()) {
  return renderToStaticMarkup(<StructuredEditor convId="item" listing={{ title: "Original" }} tab="mercari"
    target={null} disabled={disabled} onChange={onChange} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  state.fields = [];
});

describe("native row layout", () => {
  it("renders saved text and choice values on the first frame", () => {
    state.fields = [field("title"), field("mercari_specifics.brand", { value: "Nike", options: ["Nike", "Adidas"] })];
    const html = render();
    expect(html).toContain('value="Original"');
    expect(choices.mock.calls[0][0].value).toBe("Nike");
  });

  it("uses native widths even for Category and preserves heading and field order", () => {
    state.fields = [
      field("mercari_specifics.categoryId", { label: "Category", width: "half", section: "Category" }),
      field("mercari_specifics.second", { width: "half", section: "Category" }),
      field("mercari_specifics.price", { width: "third" }),
      field("mercari_specifics.weight", { width: "quarter" }),
      field("mercari_specifics.description", { input_type: "textarea" }),
    ];
    const html = render();
    expect(html).toContain("@xl:grid-cols-12");
    expect(html).toMatch(/data-listing-row="mercari_specifics.categoryId" class="group\/field min-w-0 @xl:col-span-6"/);
    expect(html).toMatch(/data-listing-row="mercari_specifics.price" class="group\/field min-w-0 @xl:col-span-4"/);
    expect(html).toMatch(/data-listing-row="mercari_specifics.weight" class="group\/field min-w-0 @xl:col-span-3"/);
    expect(html).toMatch(/data-listing-row="mercari_specifics.description" class="group\/field min-w-0 col-span-full"/);
    const positions = state.fields.map((row) => html.indexOf(`data-listing-row="${row.path}"`));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    expect(html).toContain("listing-section-mercari-0-category");
  });

  it("keeps a half-width control on its own full-width row", () => {
    state.fields = [field("mercari_specifics.price", { control_width: "half" })];
    const html = render();
    expect(html).toContain('data-listing-row="mercari_specifics.price" class="group/field min-w-0 col-span-full"');
    expect(html).toContain('class="@xl:w-1/2"');
  });
});

describe("editor busy state", () => {
  it("disables text, dates, notes and segmented controls together", () => {
    state.fields = [
      field("title"),
      field("description", { input_type: "textarea" }),
      field("mercari_specifics.date", { input_type: "datetime-local" }),
      field("mercari_specifics.type", { input_type: "segmented", options: ["One", "Two"] }),
    ];
    const html = render(true);
    expect(html).toMatch(/<fieldset[^>]*disabled=""[^>]*aria-busy="true"/);
    const inputs = html.match(/<(?:input|textarea|button)\b[^>]*>/g) ?? [];
    expect(inputs.length).toBeGreaterThanOrEqual(5);
    expect(inputs.every((tag) => tag.includes('disabled=""'))).toBe(true);
  });

  it.each([true, false])("guards choice callbacks while busy=%s, including portal selections", (disabled) => {
    state.fields = [field("mercari_specifics.closure", { options: ["Zip", "Button"] })];
    const save = vi.fn();
    render(disabled, save);
    const props = choices.mock.calls[0][0] as { disabled: boolean; onChange: (value: string) => void };
    expect(props.disabled).toBe(disabled);
    props.onChange("Zip");
    if (disabled) expect(save).not.toHaveBeenCalled();
    else expect(save).toHaveBeenCalledWith({ title: "Original", mercari_specifics: { closure: "Zip" } });
  });

  it("also guards locked choices and inventory callbacks", () => {
    state.fields = [field("mercari_specifics.closure", { options: ["Zip"], locked: true }),
      field("mercari_specifics.inventoryRows", { input_type: "inventory" })];
    const save = vi.fn();
    render(true, save);
    (choices.mock.calls[0][0] as { onChange: (value: string) => void }).onChange("Zip");
    const inventory = inventories.mock.calls[0][0] as {
      disabled: boolean; onChange: (rows: unknown[]) => void; onSizeSystem: (system: string, rows: unknown[]) => void;
    };
    expect(inventory.disabled).toBe(true);
    inventory.onChange([]);
    inventory.onSizeSystem("eu", []);
    expect(save).not.toHaveBeenCalled();
  });
});

describe("General form", () => {
  it("shows its sections in filling order with More details folded", () => {
    const html = renderToStaticMarkup(<StructuredEditor convId="item" tab="general" target={null} disabled={false} onChange={vi.fn()}
      listing={{ title: "Tee", category_path: "Men > Shirts", price: 20, sku: "A-1", quantity: 1 }} />);
    const headings = ["Item", "Condition &amp; details", "Pricing", "Description", "Color &amp; department", "Shipping", "More details"]
      .map((title) => html.indexOf(`>${title}<`));
    expect(headings.every((position) => position > 0)).toBe(true);
    expect(headings).toEqual([...headings].sort((a, b) => a - b));
    expect(html).toMatch(/aria-expanded="false"[^>]*>More details/);
    // Folded, the rows are rendered but hidden; the count still shows.
    expect(html).toMatch(/<div [^>]*hidden=""[^>]*data-slot="collapsible-panel".*data-listing-row="sku"/s);
    expect(html).toContain("2 of 3 filled");
    // A section with a single field does not count it.
    expect(html).not.toContain("1 of 1 filled");
  });

  it("opens More details when an Edit link points at a field inside it", () => {
    const html = renderToStaticMarkup(<StructuredEditor convId="item" tab="general" target={{ field: "sku", sequence: 1 }}
      disabled={false} onChange={vi.fn()} listing={{ title: "Tee" }} />);
    expect(html).toMatch(/aria-expanded="true"[^>]*>More details/);
  });
});
