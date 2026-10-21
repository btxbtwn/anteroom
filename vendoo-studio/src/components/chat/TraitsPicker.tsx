// Adapted from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: apps/web/src/components/chat/TraitsPicker.tsx
// (TraitsMenuContent) and CompactComposerControlsMenu.tsx.
//
// The composer's "…" control: T3's compact controls menu holding the traits
// menu. Studio's one trait is effort, and it lists only the levels the chosen
// model supports. T3's descriptor machinery (thinking, fast mode, context
// window, ultrathink) has no Studio counterpart and is left out.
import { memo } from "react";
import { EllipsisIcon } from "lucide-react";
import { Badge } from "../ui/badge";
import { Menu, MenuGroup, MenuPopup, MenuRadioGroup, MenuRadioItem, MenuTrigger } from "../ui/menu";
import { ComposerControl, ComposerControlIcon, type ComposerControlSize } from "./ComposerControl";
import type { EffortChoice } from "./useComposerModel";

function DefaultBadge() {
  return (
    <Badge variant="outline" size="sm" className="min-w-0">
      Default
    </Badge>
  );
}

export const TraitsMenuContent = memo(function TraitsMenuContent({
  effort,
  onEffortChange,
}: {
  effort: EffortChoice;
  onEffortChange: (value: string) => void;
}) {
  return (
    <MenuGroup>
      <div className="px-2 pt-1.5 pb-1 font-medium text-muted-foreground text-xs">Effort</div>
      <MenuRadioGroup value={effort.value} onValueChange={(value) => onEffortChange(String(value))}>
        {effort.options.map((option) => (
          <MenuRadioItem
            key={option.value}
            value={option.value}
            hideIndicator
            // Base UI keeps radio menus open by default. Close on pick so
            // the traits menu behaves like the model picker.
            closeOnClick
          >
            <span className="flex w-full min-w-0 flex-col">
              <span className="flex min-w-0 items-center gap-1">
                <span className="min-w-0 truncate">{option.label}</span>
                {option.isDefault ? <DefaultBadge /> : null}
              </span>
            </span>
          </MenuRadioItem>
        ))}
      </MenuRadioGroup>
    </MenuGroup>
  );
});

export const CompactComposerControlsMenu = memo(function CompactComposerControlsMenu(props: {
  effort: EffortChoice;
  onEffortChange: (value: string) => void;
  size?: ComposerControlSize;
  disabled?: boolean;
}) {
  const size = props.size ?? "sm";
  const effortLabel =
    props.effort.options.find((option) => option.value === props.effort.value)?.label ?? props.effort.value;
  return (
    <Menu>
      <MenuTrigger
        render={
          <ComposerControl
            size={size}
            className="shrink-0"
            aria-label={`More composer controls (effort: ${effortLabel})`}
            disabled={props.disabled}
          />
        }
      >
        <ComposerControlIcon icon={EllipsisIcon} size={size} />
      </MenuTrigger>
      <MenuPopup align="start" side="top">
        <TraitsMenuContent effort={props.effort} onEffortChange={props.onEffortChange} />
      </MenuPopup>
    </Menu>
  );
});
