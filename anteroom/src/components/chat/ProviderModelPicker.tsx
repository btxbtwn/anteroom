// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: apps/web/src/components/chat/ProviderModelPicker.tsx.
//
// The composer footer's model trigger (provider mark, model name, chevron) and
// the popover holding ModelPickerContent. Copied as written apart from Studio's
// data: one instance per provider, no multi-model selection, no ChatGPT
// sharing control, and no configurable keybinding for the trigger tooltip.
import { memo, useEffect, useState } from "react";
import type { ListingProviderId as ProviderInstanceId, ModelFavorite } from "../../api/types";
import { Badge } from "../ui/badge";
import { Popover, PopoverPopup, PopoverTrigger } from "../ui/popover";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { cn } from "../../lib/utils";
import { ModelPickerContent, resolveModelPickerSelectedModel } from "./ModelPickerContent";
import { ProviderInstanceIcon } from "./ProviderInstanceIcon";
import {
  type ModelEsque,
  getTriggerDisplayModelLabel,
  getTriggerDisplayModelName,
} from "./providerIconUtils";
import { ComposerControl, ComposerControlChevron, type ComposerControlSize } from "./ComposerControl";
import type { ProviderInstanceEntry } from "./useComposerModel";

export const ProviderModelPicker = memo(function ProviderModelPicker(props: {
  activeInstanceId: ProviderInstanceId;
  model: string;
  instanceEntries: ReadonlyArray<ProviderInstanceEntry>;
  modelOptionsByInstance: ReadonlyMap<ProviderInstanceId, ReadonlyArray<ModelEsque>>;
  favorites: ReadonlyArray<ModelFavorite>;
  onToggleFavorite: (instanceId: ProviderInstanceId, model: string) => void;
  size?: ComposerControlSize;
  disabled?: boolean;
  triggerClassName?: string;
  onInstanceModelChange: (instanceId: ProviderInstanceId, model: string) => void;
}) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const size = props.size ?? "sm";

  const activeEntry =
    props.instanceEntries.find((entry) => entry.instanceId === props.activeInstanceId) ?? null;

  const selectedInstanceOptions = props.modelOptionsByInstance.get(props.activeInstanceId) ?? [];
  const selectedModel =
    resolveModelPickerSelectedModel({ model: props.model, options: selectedInstanceOptions }) ??
    selectedInstanceOptions[0];
  const triggerTitle = selectedModel
    ? getTriggerDisplayModelName(selectedModel)
    : props.model || "Choose model";
  const triggerLabel = selectedModel
    ? `${getTriggerDisplayModelLabel(selectedModel)}${selectedModel.isUnavailable ? " (Unavailable)" : ""}`
    : triggerTitle;

  useEffect(() => {
    if (!isMenuOpen) {
      return;
    }

    const { documentElement, body } = document;
    const previousDocumentOverscrollBehavior = documentElement.style.overscrollBehavior;
    const previousBodyOverflow = body.style.overflow;
    const previousBodyPaddingRight = body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - documentElement.clientWidth;

    documentElement.style.overscrollBehavior = "contain";
    body.style.overflow = "hidden";
    if (scrollbarWidth > 0) {
      body.style.paddingRight = `${scrollbarWidth}px`;
    }

    const shouldAllowOverlayScroll = (target: EventTarget | null) => {
      return target instanceof Element && target.closest("[data-model-picker-content]");
    };
    const preventBackgroundWheel = (event: WheelEvent) => {
      if (shouldAllowOverlayScroll(event.target)) {
        return;
      }
      event.preventDefault();
    };
    const preventBackgroundTouchMove = (event: TouchEvent) => {
      if (shouldAllowOverlayScroll(event.target)) {
        return;
      }
      event.preventDefault();
    };

    document.addEventListener("wheel", preventBackgroundWheel, { capture: true, passive: false });
    document.addEventListener("touchmove", preventBackgroundTouchMove, {
      capture: true,
      passive: false,
    });

    return () => {
      document.removeEventListener("wheel", preventBackgroundWheel, { capture: true });
      document.removeEventListener("touchmove", preventBackgroundTouchMove, { capture: true });
      documentElement.style.overscrollBehavior = previousDocumentOverscrollBehavior;
      body.style.overflow = previousBodyOverflow;
      body.style.paddingRight = previousBodyPaddingRight;
    };
  }, [isMenuOpen]);

  const handleInstanceModelChange = (instanceId: ProviderInstanceId, model: string) => {
    if (props.disabled) return;
    props.onInstanceModelChange(instanceId, model);
    setIsMenuOpen(false);
  };

  return (
    <Popover
      open={isMenuOpen}
      onOpenChange={(open) => {
        if (props.disabled) {
          setIsMenuOpen(false);
          return;
        }
        setIsMenuOpen(open);
      }}
    >
      <PopoverTrigger
        render={
          <ComposerControl
            aria-label={triggerLabel}
            size={size}
            data-chat-provider-model-picker="true"
            className={cn(
              "min-w-0 shrink justify-between whitespace-nowrap max-w-48 sm:max-w-56",
              props.triggerClassName,
            )}
            disabled={props.disabled}
          />
        }
      >
        <span
          className={cn("flex min-w-0 flex-1 items-center", size === "xs" ? "gap-1" : "gap-1.5")}
        >
          {activeEntry ? (
            <ProviderInstanceIcon
              driverKind={activeEntry.instanceId}
              displayName={activeEntry.displayName}
              className="size-4"
              iconClassName="size-4"
            />
          ) : null}
          <Tooltip>
            <TooltipTrigger
              render={
                <span
                  className="min-w-0 flex-1 overflow-hidden truncate"
                  data-chat-provider-model-picker-label="true"
                />
              }
            >
              {triggerTitle}
            </TooltipTrigger>
            <TooltipPopup side="top">{triggerLabel}</TooltipPopup>
          </Tooltip>
          {selectedModel?.isUnavailable ? (
            <Badge variant="outline" size="sm">
              Unavailable
            </Badge>
          ) : null}
        </span>
        <span aria-hidden="true" className="flex items-center">
          <ComposerControlChevron size={size} />
        </span>
      </PopoverTrigger>
      <PopoverPopup align="start" side="top" className="before:hidden" padding="none" touchBackdrop>
        <ModelPickerContent
          activeInstanceId={props.activeInstanceId}
          model={props.model}
          instanceEntries={props.instanceEntries}
          modelOptionsByInstance={props.modelOptionsByInstance}
          favorites={props.favorites}
          onToggleFavorite={props.onToggleFavorite}
          onRequestClose={() => setIsMenuOpen(false)}
          onInstanceModelChange={handleInstanceModelChange}
        />
      </PopoverPopup>
    </Popover>
  );
});
