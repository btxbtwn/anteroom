import { PlusIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { StudioOptions, VirtualModel } from "../api/types";
import { cn } from "../lib/utils";
import { BACKDROP_COLORS, DEFAULT_BACKDROP, isHexColor } from "../photoColors";
import { normalizeStudio } from "../photoEdits";
import { SettingsSelect, type SettingsSelectOption } from "./settings/SettingsLayout";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";

export function ToolGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-2 font-medium text-muted-foreground text-xs">{label}</legend>
      {children}
    </fieldset>
  );
}

/** Colour swatches, plus the system picker for any other colour. */
export function Swatches({ label, colors, value, onChange }: {
  label: string;
  colors: readonly string[];
  value: string;
  onChange: (color: string) => void;
}) {
  const custom = !colors.some((color) => color.toUpperCase() === value.toUpperCase());
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={label}>
      {colors.map((color) => {
        const selected = color.toUpperCase() === value.toUpperCase();
        return (
          <button
            key={color}
            type="button"
            aria-label={`${label} ${color}`}
            aria-pressed={selected}
            title={color}
            className={cn("size-6 rounded-md border border-border focus-visible:outline-2 focus-visible:outline-ring",
              selected && "ring-2 ring-foreground ring-offset-1 ring-offset-background")}
            style={{ backgroundColor: color }}
            onClick={() => onChange(color)}
          />
        );
      })}
      <label className={cn("relative size-6 overflow-hidden rounded-md border border-border", custom && "ring-2 ring-foreground ring-offset-1 ring-offset-background")}
        title="Any colour">
        <span className="sr-only">{`${label}: any colour`}</span>
        <PlusIcon aria-hidden className="pointer-events-none absolute inset-0 z-10 m-auto size-3.5 rounded-sm bg-background/80 text-foreground" />
        <input type="color" className="absolute -inset-2 size-10 cursor-pointer" value={isHexColor(value) ? value.toLowerCase() : undefined}
          onChange={(event) => onChange(event.target.value.toUpperCase())} />
      </label>
    </div>
  );
}

type Effect = "none" | "ghost_mannequin" | "flat_lay" | "virtual_model";
const AUTO = { value: "auto", label: "Auto" } as const;
const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
const MODEL_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["model"]>>[] = [AUTO,
  ...(["avery", "sam", "taylor", "kendall", "jordan", "casey", "maya", "reece", "lena", "julia", "jackson", "sophia", "emma", "ava", "zoe", "fiona"] as const)
    .map((value) => ({ value, label: titleCase(value) }))];
const SCENE_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["scene"]>>[] = [AUTO,
  { value: "studio", label: "Studio" }, { value: "coloredstudio", label: "Coloured studio" },
  { value: "concretestudio", label: "Concrete studio" }, { value: "street", label: "Street" },
  { value: "cafe", label: "Café" }, { value: "bedroom", label: "Bedroom" }, { value: "library", label: "Library" },
  { value: "businessdistrict", label: "Business district" }, { value: "factory", label: "Factory" },
  { value: "beach", label: "Beach" }, { value: "pool", label: "Pool" }, { value: "tropical", label: "Tropical" },
  { value: "sunset", label: "Sunset" }, { value: "goldenlight", label: "Golden light" }, { value: "nightlights", label: "Night lights" },
  { value: "forest", label: "Forest" }, { value: "flowers", label: "Flowers" }, { value: "countryside", label: "Countryside" },
  { value: "mountain", label: "Mountain" }, { value: "desert", label: "Desert" }, { value: "latincity", label: "Latin city" },
  { value: "asiancity", label: "Asian city" }];
const POSE_OPTIONS: SettingsSelectOption<NonNullable<VirtualModel["pose"]>>[] = [AUTO,
  { value: "standing", label: "Standing" }, { value: "34turn", label: "Three-quarter turn" },
  { value: "powerstance", label: "Power stance" }, { value: "walkingforward", label: "Walking forward" },
  { value: "handinpocket", label: "Hand in pocket" }, { value: "crossedarms", label: "Crossed arms" },
  { value: "back", label: "Back" }, { value: "overtheshoulder", label: "Over the shoulder" },
  { value: "seated", label: "Seated" }, { value: "adjustingclothing", label: "Adjusting clothing" },
  { value: "playfulspin", label: "Playful spin" }];

/** The AI Studio choices, as the seller picks them; ``normalizeStudio`` turns them into a valid request. */
export interface StudioChoice {
  removeBackground: boolean;
  background: string;
  effect: Effect;
  shadow: boolean;
  keep: string;
  exclude: string;
  model: VirtualModel;
}

export function studioRequest(choice: StudioChoice): StudioOptions {
  if (choice.effect === "virtual_model") return { virtual_model: choice.model };
  return normalizeStudio({
    remove_background: choice.removeBackground,
    background: choice.background,
    ghost_mannequin: choice.effect === "ghost_mannequin",
    flat_lay: choice.effect === "flat_lay",
    shadow: choice.shadow,
    keep: choice.keep,
    exclude: choice.exclude,
  });
}

export const defaultStudioChoice: StudioChoice = {
  removeBackground: true, background: DEFAULT_BACKDROP, effect: "none", shadow: false,
  keep: "", exclude: "",
  model: { model: "auto", scene: "auto", pose: "auto" },
};

export function StudioOptionsFields({ choice, onChange, running = false }: {
  choice: StudioChoice;
  onChange: (choice: StudioChoice) => void;
  running?: boolean;
}) {
  const modelScene = choice.effect === "virtual_model";
  const cutsOut = choice.effect === "ghost_mannequin" || choice.effect === "flat_lay" || choice.shadow;
  const guidesCutout = (choice.removeBackground || cutsOut) && !modelScene;
  const update = (change: Partial<StudioChoice>) => onChange({ ...choice, ...change });
  return (
    <>
      <ToolGroup label="Background">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={choice.removeBackground || cutsOut} disabled={modelScene || cutsOut}
            onCheckedChange={(checked) => update({ removeBackground: checked === true })} />
          Remove background
        </label>
        {guidesCutout ? (
          <Swatches label="Background colour" colors={BACKDROP_COLORS} value={choice.background}
            onChange={(background) => update({ background })} />
        ) : null}
      </ToolGroup>
      {guidesCutout ? (
        <ToolGroup label="Subject">
          <p className="text-muted-foreground text-xs">
            Optional. Singular words Photoroom should keep or leave out (shirt, ruler, stand).
          </p>
          <label className="flex flex-col gap-1 text-xs">
            Keep
            <Input aria-label="Keep in cutout" value={choice.keep} maxLength={200} placeholder="shirt"
              disabled={running} onChange={(event) => update({ keep: event.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Leave out
            <Input aria-label="Leave out of cutout" value={choice.exclude} maxLength={200}
              placeholder="ruler, stand, hanger" disabled={running}
              onChange={(event) => update({ exclude: event.target.value })} />
          </label>
        </ToolGroup>
      ) : null}
      <ToolGroup label="AI restyle">
        {([
          ["ghost_mannequin", "Ghost mannequin", "Shows clothing as if worn, with no mannequin."],
          ["flat_lay", "Flat lay", "A styled overhead layout."],
          ["virtual_model", "Virtual model", "A model wears the item in a scene of its own."],
        ] as const).map(([effect, label, description]) => (
          <label key={effect} className="flex items-start gap-2 text-sm">
            <Checkbox className="mt-0.5" checked={choice.effect === effect}
              onCheckedChange={(checked) => update(checked === true
                ? { effect, ...(effect === "virtual_model"
                  ? { shadow: false, removeBackground: false, keep: "", exclude: "" } : {}) }
                : { effect: "none" })} />
            <span className="flex flex-col">
              {label}
              <span className="text-muted-foreground text-xs">{description}</span>
            </span>
          </label>
        ))}
        <label className="flex items-start gap-2 text-sm">
          <Checkbox className="mt-0.5" checked={choice.shadow} disabled={modelScene}
            onCheckedChange={(checked) => update({ shadow: checked === true })} />
          <span className="flex flex-col">
            Natural shadow
            <span className="text-muted-foreground text-xs">A soft shadow under the item.</span>
          </span>
        </label>
        {modelScene ? (
          <div className="grid gap-2">
            <label className="flex items-center justify-between gap-2 text-xs">
              Model
              <SettingsSelect aria-label="Virtual model" className="w-40" value={choice.model.model ?? "auto"} options={MODEL_OPTIONS}
                onChange={(model) => update({ model: { ...choice.model, model } })} />
            </label>
            <label className="flex items-center justify-between gap-2 text-xs">
              Scene
              <SettingsSelect aria-label="Scene" className="w-40" value={choice.model.scene ?? "auto"} options={SCENE_OPTIONS}
                onChange={(scene) => update({ model: { ...choice.model, scene } })} />
            </label>
            <label className="flex items-center justify-between gap-2 text-xs">
              Pose
              <SettingsSelect aria-label="Pose" className="w-40" value={choice.model.pose ?? "auto"} options={POSE_OPTIONS}
                onChange={(pose) => update({ model: { ...choice.model, pose } })} />
            </label>
          </div>
        ) : null}
      </ToolGroup>
    </>
  );
}
