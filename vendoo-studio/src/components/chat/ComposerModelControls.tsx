/**
 * The composer footer's model and effort pickers, laid out like T3 Code's
 * ProviderModelPicker (provider rail + model list in a popover) and
 * TraitsPicker (an effort menu). Picking saves the same settings
 * Settings → Providers edits.
 *
 * Markup adapted from T3 Code (MIT, © 2026 T3 Tools Inc.):
 * components/chat/ProviderModelPicker.tsx, ModelPickerContent.tsx,
 * ModelPickerSidebar.tsx, ModelListRow.tsx and TraitsPicker.tsx.
 */
import { useState } from "react";
import { BrainIcon, CheckIcon, SearchIcon } from "lucide-react";
import type { ListingProviderId } from "../../api/types";
import { cn } from "../../lib/utils";
import { ProviderLogo } from "../ProviderLogo";
import { Menu, MenuGroup, MenuPopup, MenuRadioGroup, MenuRadioItem, MenuTrigger } from "../ui/menu";
import { Popover, PopoverPopup, PopoverTrigger } from "../ui/popover";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { ComposerControl, ComposerControlChevron, ComposerControlIcon } from "./ComposerControl";
import { useComposerModel, type ProviderModels } from "./useComposerModel";

function ModelRail({
  providers,
  selected,
  onSelect,
}: {
  providers: ProviderModels[];
  selected: ListingProviderId;
  onSelect: (id: ListingProviderId) => void;
}) {
  return (
    <div className="w-11 shrink-0 overflow-hidden bg-muted/30" data-model-picker-sidebar="true" aria-label="Providers" role="toolbar" aria-orientation="vertical">
      <div className="relative flex min-h-full flex-col gap-1 p-1">
        {providers.map((provider) => {
          const isSelected = provider.id === selected;
          const button = (
            <button
              type="button"
              className={cn(
                "relative isolate flex w-full cursor-pointer aspect-square items-center justify-center rounded-md transition-colors hover:bg-foreground/10 focus-visible:bg-foreground/10 focus-visible:outline-none",
                isSelected && "bg-background shadow-xs",
                !provider.ready && "opacity-50 cursor-not-allowed hover:bg-transparent",
              )}
              onClick={() => provider.ready && onSelect(provider.id)}
              disabled={!provider.ready}
              aria-pressed={isSelected}
              aria-label={provider.ready ? provider.label : `${provider.label} is not set up`}
            >
              <ProviderLogo id={provider.id} label={provider.label} size={20} />
            </button>
          );
          return (
            <Tooltip key={provider.id}>
              <TooltipTrigger render={<span className="relative block w-full" />}>{button}</TooltipTrigger>
              <TooltipPopup side="left">
                {provider.ready ? provider.label : `${provider.label}: set it up in Settings → Providers`}
              </TooltipPopup>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

function ModelList({
  provider,
  current,
  onPick,
}: {
  provider: ProviderModels;
  current: { provider: ListingProviderId | null; model: string };
  onPick: (model: string) => void;
}) {
  const [query, setQuery] = useState("");
  const models = provider.models.filter((model) => model.label.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden border-l border-border/70 bg-muted/40">
      <label className="flex h-9 shrink-0 items-center gap-2 border-b border-border/70 px-3">
        <SearchIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground/70" />
        <input
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
          placeholder="Search models..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="min-h-0 flex-1 overflow-y-auto p-1" role="listbox" aria-label={`${provider.label} models`}>
        {provider.loading ? <p className="px-2 py-2 text-muted-foreground text-xs">Loading models…</p> : null}
        {models.map((model) => {
          const selected = current.provider === provider.id && current.model === model.value;
          return (
            <button
              type="button"
              role="option"
              aria-selected={selected}
              key={model.value}
              onClick={() => onPick(model.value)}
              className="group relative flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left outline-none hover:bg-accent focus-visible:bg-accent"
            >
              <span className="min-w-0 flex-1">
                <span className="block min-w-0 truncate text-xs font-medium leading-snug">{model.label}</span>
                <span className="mt-1 flex items-center gap-1.5">
                  <ProviderLogo id={provider.id} label={provider.label} size={12} />
                  <span className="truncate text-xs font-normal leading-snug text-muted-foreground/70">{provider.label}</span>
                </span>
              </span>
              {selected ? <CheckIcon className="size-3.5 shrink-0" aria-hidden="true" /> : null}
            </button>
          );
        })}
        {!provider.loading && models.length === 0 ? (
          <p className="px-2 py-2 text-muted-foreground text-xs">{query ? "No models found" : "No models"}</p>
        ) : null}
      </div>
      {provider.error ? (
        <p className="shrink-0 border-t border-border/70 px-3 py-2 text-xs leading-snug text-muted-foreground">{provider.error}</p>
      ) : null}
    </div>
  );
}

export function ComposerModelControls({ disabled = false }: { disabled?: boolean }) {
  const model = useComposerModel();
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState<ListingProviderId | null>(null);
  if (model.loading || !model.current) return null;
  const shown = model.providers.find((provider) => provider.id === (browsing ?? model.active)) ?? model.current;
  const triggerLabel = model.currentModel || model.current.label;
  const effortLabel = model.effort?.options.find((option) => option.value === model.effort?.value)?.label;
  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next && !disabled);
          if (!next) setBrowsing(null);
        }}
      >
        <PopoverTrigger
          render={
            <ComposerControl
              aria-label={`${model.current.label} ${triggerLabel}`}
              data-chat-provider-model-picker="true"
              className="-ms-2.5 min-w-13 min-w-0 shrink justify-between whitespace-nowrap max-w-56"
              disabled={disabled || model.pending}
            />
          }
        >
          <span className="flex min-w-0 flex-1 items-center gap-1.5">
            <ProviderLogo id={model.current.id} label={model.current.label} size={16} />
            <span className="min-w-0 flex-1 overflow-hidden truncate" data-chat-provider-model-picker-label="true">
              {triggerLabel}
            </span>
          </span>
          <span aria-hidden="true" className="flex items-center">
            <ComposerControlChevron />
          </span>
        </PopoverTrigger>
        <PopoverPopup align="start" side="top" className="before:hidden" padding="none">
          <div className="relative flex max-h-86.5 w-screen max-w-90 flex-row overflow-hidden" data-model-picker-content="true">
            <ModelRail providers={model.providers} selected={shown.id} onSelect={setBrowsing} />
            <ModelList
              provider={shown}
              current={{ provider: model.active, model: model.currentModel }}
              onPick={(value) => {
                model.selectModel(shown.id, value);
                setOpen(false);
                setBrowsing(null);
              }}
            />
          </div>
        </PopoverPopup>
      </Popover>
      {model.effort ? (
        <Menu>
          <MenuTrigger
            render={
              <ComposerControl
                aria-label={`Effort: ${effortLabel ?? model.effort.value}`}
                className="shrink-0 whitespace-nowrap"
                disabled={disabled || model.pending}
              />
            }
          >
            <ComposerControlIcon icon={BrainIcon} />
            <span data-composer-control-label>{effortLabel ?? model.effort.value}</span>
            <ComposerControlChevron />
          </MenuTrigger>
          <MenuPopup align="start" side="top">
            <MenuGroup>
              <div className="px-2 pt-1.5 pb-1 font-medium text-muted-foreground text-xs">Effort</div>
              <MenuRadioGroup value={model.effort.value} onValueChange={(value) => model.selectEffort(String(value))}>
                {model.effort.options.map((option) => (
                  <MenuRadioItem key={option.value} value={option.value} hideIndicator closeOnClick>
                    <span className="flex w-full min-w-0 items-center justify-between gap-3">
                      <span className="min-w-0 truncate">{option.label}</span>
                      {option.value === model.effort?.value ? <CheckIcon className="size-3.5" aria-hidden /> : null}
                    </span>
                  </MenuRadioItem>
                ))}
              </MenuRadioGroup>
            </MenuGroup>
          </MenuPopup>
        </Menu>
      ) : null}
      {model.error ? <span className="truncate text-destructive-foreground text-xs" role="alert">{model.error}</span> : null}
    </>
  );
}
