// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: apps/web/src/components/chat/ModelPickerContent.tsx.
//
// The picker body: provider rail, "Search models..." input, and the model list
// with ⌘1…⌘9 jump hints and favorite stars. Kept as close to T3 as Studio's
// data allows. Left out: thread-locked providers, multi-model selection, the
// legacy-models section and LegendList virtualization (Studio's catalogs are
// short enough to render whole). T3's configurable keybindings become their
// default bindings: mod+1…9 picks, mod+shift+↑/↓ switches provider.
import {
  memo,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ListingProviderId as ProviderInstanceId, ModelFavorite } from "../../api/types";
import { ModelListRow } from "./ModelListRow";
import { ModelPickerSidebar } from "./ModelPickerSidebar";
import { modelPickerModelKey, parseModelPickerModelKey } from "./modelPickerKeys";
import { rankModelPickerSearch } from "./modelPickerSearch";
import {
  Combobox,
  ComboboxEmpty,
  ComboboxSearchInput,
  ComboboxListVirtualized,
} from "../ui/combobox";
import type { ModelEsque } from "./providerIconUtils";
import { cn } from "../../lib/utils";
import { getVirtualizedScrollFadeClassName } from "../ui/scroll-area";
import { TooltipProvider } from "../ui/tooltip";
import { providerModelKey, sortProviderModelItems } from "./modelOrdering";
import type { ProviderInstanceEntry } from "./useComposerModel";

type ModelPickerItem = {
  slug: string;
  name: string;
  shortName?: string;
  subProvider?: string;
  badge?: "new";
  instanceId: ProviderInstanceId;
  driverKind: ProviderInstanceId;
  instanceDisplayName: string;
  isUnavailable?: boolean | undefined;
};

export const MODEL_PICKER_JUMP_COUNT = 9;

export function resolveModelPickerSelectedModel(input: {
  model: string;
  options: ReadonlyArray<ModelEsque>;
}) {
  return input.options.find((option) => option.slug === input.model);
}

export function isMacPlatform(platform: string = typeof navigator === "undefined" ? "" : navigator.platform): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** T3's label for `mod+N`: ⌘N on a Mac, Ctrl+N elsewhere. */
export function modelPickerJumpLabel(index: number, mac: boolean = isMacPlatform()): string {
  return mac ? `⌘${index + 1}` : `Ctrl+${index + 1}`;
}

type ModelPickerCommand = { kind: "jump"; index: number } | { kind: "provider"; direction: 1 | -1 };

/** T3's default model picker bindings while the picker is open. */
export function modelPickerCommandFromEvent(
  event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">,
  mac: boolean = isMacPlatform(),
): ModelPickerCommand | null {
  const mod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  if (!mod || event.altKey) return null;
  if (event.shiftKey) {
    if (event.key === "ArrowUp") return { kind: "provider", direction: -1 };
    if (event.key === "ArrowDown") return { kind: "provider", direction: 1 };
    return null;
  }
  if (/^[1-9]$/.test(event.key)) return { kind: "jump", index: Number(event.key) - 1 };
  return null;
}

export function adjacentModelPickerProvider(input: {
  entries: ReadonlyArray<ProviderInstanceEntry>;
  selectedInstanceId: ProviderInstanceId | "favorites";
  direction: 1 | -1;
}) {
  const providers: Array<ProviderInstanceId | "favorites"> = [
    "favorites",
    ...input.entries.filter((entry) => entry.ready).map((entry) => entry.instanceId),
  ];
  const index = providers.indexOf(input.selectedInstanceId);
  return providers[
    index < 0
      ? input.direction === 1
        ? 0
        : providers.length - 1
      : (index + input.direction + providers.length) % providers.length
  ]!;
}

// Owns the list's scroll fades, which change on every scroll without touching
// the picker's model lists.
function ModelPickerList({
  itemKeys,
  renderItem,
}: {
  itemKeys: ReadonlyArray<string>;
  renderItem: (props: { item: string; index: number }) => ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showTopScrollFade, setShowTopScrollFade] = useState(false);
  const [showBottomScrollFade, setShowBottomScrollFade] = useState(false);
  const updateScrollFades = () => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) {
      return;
    }
    const maxScrollOffset = Math.max(0, scrollElement.scrollHeight - scrollElement.clientHeight);
    setShowTopScrollFade(scrollElement.scrollTop > 1);
    setShowBottomScrollFade(maxScrollOffset - scrollElement.scrollTop > 1);
  };

  const itemListKey = JSON.stringify(itemKeys);
  const measureScrollFades = useEffectEvent(updateScrollFades);
  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(() => measureScrollFades());
    return () => window.cancelAnimationFrame(frame);
  }, [itemListKey]);

  if (itemKeys.length === 0) return null;
  return (
    <div className="relative flex min-h-0 flex-col overflow-hidden pr-px">
      <ComboboxListVirtualized className="flex h-auto min-h-0 flex-1 flex-col not-empty:p-0">
        <div
          ref={scrollRef}
          onScroll={updateScrollFades}
          className={cn(
            "scrollbar-gutter-stable min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain py-1.5 [&::-webkit-scrollbar-track]:my-2",
            getVirtualizedScrollFadeClassName({
              top: showTopScrollFade,
              bottom: showBottomScrollFade,
            }),
          )}
        >
          <div className="flex flex-col gap-0.5 pl-2 pr-px">
            {itemKeys.map((item, index) => renderItem({ item, index }))}
          </div>
        </div>
      </ComboboxListVirtualized>
    </div>
  );
}

// Capture phase, so model and provider shortcuts win over the composer underneath.
function WindowKeyDownListener({ onKeyDown }: { onKeyDown: (event: KeyboardEvent) => void }) {
  const handleKeyDown = useEffectEvent(onKeyDown);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => handleKeyDown(event);
    window.addEventListener("keydown", listener, true);
    return () => {
      window.removeEventListener("keydown", listener, true);
    };
  }, []);
  return null;
}

export const ModelPickerContent = memo(function ModelPickerContent(props: {
  activeInstanceId: ProviderInstanceId;
  model: string;
  instanceEntries: ReadonlyArray<ProviderInstanceEntry>;
  modelOptionsByInstance: ReadonlyMap<ProviderInstanceId, ReadonlyArray<ModelEsque>>;
  favorites: ReadonlyArray<ModelFavorite>;
  onToggleFavorite: (instanceId: ProviderInstanceId, model: string) => void;
  onRequestClose?: () => void;
  onInstanceModelChange: (instanceId: ProviderInstanceId, model: string) => void;
}) {
  const { modelOptionsByInstance, instanceEntries, onInstanceModelChange, onRequestClose, favorites } = props;
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const pickerContentRef = useRef<HTMLDivElement>(null);
  const highlightedModelKeyRef = useRef<string | null>(null);
  const activeModel = resolveModelPickerSelectedModel({
    model: props.model,
    options: modelOptionsByInstance.get(props.activeInstanceId) ?? [],
  });
  const activeModelSlug = activeModel?.slug ?? props.model;
  const activeModelKey = activeModelSlug
    ? modelPickerModelKey(props.activeInstanceId, activeModelSlug)
    : null;
  const [selectedInstanceId, setSelectedInstanceId] = useState<ProviderInstanceId | "favorites">(
    () => (favorites.length > 0 ? "favorites" : props.activeInstanceId),
  );
  const favoritesSet = new Set(favorites.map((fav) => providerModelKey(fav.provider, fav.model)));
  const [searchHeight, setSearchHeight] = useState(0);

  const focusSearchInput = () => {
    searchInputRef.current?.focus({ preventScroll: true });
  };

  const handleSelectInstance = (instanceId: ProviderInstanceId | "favorites") => {
    setSelectedInstanceId(instanceId);
    if (window.matchMedia?.("(hover: none)").matches) return;
    window.requestAnimationFrame(() => {
      focusSearchInput();
    });
  };

  useLayoutEffect(() => {
    // On a phone, focusing search would raise the keyboard over the list the seller came to tap.
    if (window.matchMedia?.("(hover: none)").matches) return;
    searchInputRef.current?.focus({ preventScroll: true });
    const frame = window.requestAnimationFrame(() => {
      searchInputRef.current?.focus({ preventScroll: true });
    });
    const timeout = window.setTimeout(() => {
      searchInputRef.current?.focus({ preventScroll: true });
    }, 0);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timeout);
    };
  }, []);

  const entryByInstanceId = new Map(instanceEntries.map((entry) => [entry.instanceId, entry]));

  const handleModelSelect = (modelSlug: string, instanceId: ProviderInstanceId) => {
    const options = modelOptionsByInstance.get(instanceId);
    if (!options?.some((option) => option.slug === modelSlug)) {
      return;
    }
    if (!entryByInstanceId.get(instanceId)?.ready) {
      return;
    }
    onInstanceModelChange(instanceId, modelSlug);
  };

  const flatModels = (() => {
    const out: ModelPickerItem[] = [];
    for (const [instanceId, models] of modelOptionsByInstance) {
      const entry = entryByInstanceId.get(instanceId);
      if (!entry?.ready) {
        continue;
      }
      for (const model of models) {
        out.push({
          slug: model.slug,
          name: model.name,
          ...(model.shortName ? { shortName: model.shortName } : {}),
          ...(model.subProvider ? { subProvider: model.subProvider } : {}),
          ...(model.badge ? { badge: model.badge } : {}),
          ...(model.isUnavailable ? { isUnavailable: true } : {}),
          instanceId,
          driverKind: entry.instanceId,
          instanceDisplayName: entry.displayName,
        });
      }
    }
    return out;
  })();

  const isSearching = searchQuery.trim().length > 0;
  const sidebarInstanceEntries = instanceEntries;
  const showSidebar = !isSearching && sidebarInstanceEntries.length > 0;
  const instanceOrder = instanceEntries.map((entry) => entry.instanceId);

  const filteredModels = (() => {
    const result = flatModels;

    if (searchQuery.trim()) {
      return rankModelPickerSearch(result, searchQuery, (model) => ({
        name: model.name,
        ...(model.shortName ? { shortName: model.shortName } : {}),
        ...(model.subProvider ? { subProvider: model.subProvider } : {}),
        driverKind: model.driverKind,
        providerDisplayName: model.instanceDisplayName,
        isFavorite: favoritesSet.has(providerModelKey(model.instanceId, model.slug)),
      }));
    }

    const shown =
      selectedInstanceId === "favorites"
        ? result.filter((m) => favoritesSet.has(providerModelKey(m.instanceId, m.slug)))
        : result.filter((m) => m.instanceId === selectedInstanceId);

    return sortProviderModelItems(shown, {
      favoriteModelKeys: favoritesSet,
      groupFavorites: selectedInstanceId !== "favorites",
      instanceOrder: selectedInstanceId === "favorites" ? instanceOrder : [],
    });
  })();

  const visibleModels = filteredModels;

  const selectedEntry =
    selectedInstanceId === "favorites" ? undefined : entryByInstanceId.get(selectedInstanceId);
  const providerNotices = instanceEntries.flatMap((entry) => {
    const shown = isSearching ? entry.ready : entry.instanceId === selectedEntry?.instanceId;
    return shown && entry.ready && entry.message
      ? [{ instanceId: entry.instanceId, notice: entry.message }]
      : [];
  });

  // The first models get the numbered jump shortcuts, in list order.
  const modelJumpModelKeys = visibleModels
    .slice(0, MODEL_PICKER_JUMP_COUNT)
    .map((model) => modelPickerModelKey(model.instanceId, model.slug));
  const filteredItemKeys = visibleModels.map((model) =>
    modelPickerModelKey(model.instanceId, model.slug),
  );
  const filteredModelByKey: ReadonlyMap<string, ModelPickerItem> = new Map(
    visibleModels.map((model) => [modelPickerModelKey(model.instanceId, model.slug), model] as const),
  );
  const mac = isMacPlatform();
  const modelJumpLabelByKey = new Map(
    modelJumpModelKeys.map((modelKey, index) => [modelKey, modelPickerJumpLabel(index, mac)] as const),
  );

  const onWindowKeyDown = (event: globalThis.KeyboardEvent) => {
    if (event.defaultPrevented || event.repeat) {
      return;
    }

    const command = modelPickerCommandFromEvent(event, mac);
    if (!command) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (command.kind === "provider") {
      const next = adjacentModelPickerProvider({
        entries: sidebarInstanceEntries,
        selectedInstanceId,
        direction: command.direction,
      });
      setSearchQuery("");
      handleSelectInstance(next);
      return;
    }

    const targetModelKey = modelJumpModelKeys[command.index];
    if (!targetModelKey) {
      return;
    }
    const model = parseModelPickerModelKey(targetModelKey);
    if (!model) {
      return;
    }
    handleModelSelect(model.slug, model.instanceId);
  };

  return (
    <TooltipProvider delay={0}>
      <WindowKeyDownListener onKeyDown={onWindowKeyDown} />
      <div
        ref={pickerContentRef}
        className="relative flex max-h-86.5 w-screen max-w-90 flex-row overflow-hidden"
        style={isSearching ? { height: searchHeight } : undefined}
        data-model-picker-content="true"
      >
        {showSidebar && (
          <ModelPickerSidebar
            selectedInstanceId={selectedInstanceId}
            onSelectInstance={handleSelectInstance}
            onFocusSearch={focusSearchInput}
            instanceEntries={sidebarInstanceEntries}
            showFavorites
          />
        )}

        <Combobox<string>
          inline
          // The rendered list is not virtualized, so Base UI indexes the shown rows
          // (T3 hands it every model and virtualizes).
          items={filteredItemKeys}
          filteredItems={filteredItemKeys}
          filter={null}
          autoHighlight
          open
          value={activeModelKey}
          onItemHighlighted={(modelKey) => {
            highlightedModelKeyRef.current = typeof modelKey === "string" ? modelKey : null;
          }}
          onValueChange={(value) => {
            if (typeof value !== "string") {
              return;
            }
            const model = parseModelPickerModelKey(value);
            if (model) {
              handleModelSelect(model.slug, model.instanceId);
            }
          }}
        >
          <div
            className={cn(
              // Without T3's measured list height, cap the column itself so the list scrolls.
              "flex max-h-86.5 min-h-0 flex-1 flex-col overflow-hidden bg-muted/40",
              showSidebar && "border-l border-border/70",
            )}
          >
            <ComboboxSearchInput
              ref={searchInputRef}
              placeholder="Search models..."
              value={searchQuery}
              onChange={(e) => {
                if (!isSearching) setSearchHeight(pickerContentRef.current?.offsetHeight ?? 0);
                setSearchQuery(e.target.value);
              }}
              onKeyDown={(e) => {
                if (
                  showSidebar &&
                  !e.altKey &&
                  !e.ctrlKey &&
                  !e.metaKey &&
                  ((e.key === "ArrowLeft" && !e.shiftKey && searchQuery.length === 0) ||
                    (e.key === "Tab" && e.shiftKey))
                ) {
                  const sidebar = e.currentTarget
                    .closest("[data-model-picker-content]")
                    ?.querySelector("[data-model-picker-sidebar]");
                  const button =
                    sidebar?.querySelector<HTMLButtonElement>(
                      'button[aria-pressed="true"]:not(:disabled)',
                    ) ?? sidebar?.querySelector<HTMLButtonElement>("button:not(:disabled)");
                  if (button) {
                    e.preventDefault();
                    e.stopPropagation();
                    button.focus();
                    return;
                  }
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  onRequestClose?.();
                  return;
                }
                if (e.key === "Enter" && highlightedModelKeyRef.current) {
                  (e as typeof e & { preventBaseUIHandler?: () => void }).preventBaseUIHandler?.();
                  e.preventDefault();
                  e.stopPropagation();
                  const model = parseModelPickerModelKey(highlightedModelKeyRef.current);
                  if (model) {
                    handleModelSelect(model.slug, model.instanceId);
                  }
                  return;
                }
                e.stopPropagation();
              }}
              onMouseDown={(e) => e.stopPropagation()}
              onTouchStart={(e) => e.stopPropagation()}
            />

            <ModelPickerList
              itemKeys={filteredItemKeys}
              renderItem={({ item: modelKey, index }) => {
                const model = filteredModelByKey.get(modelKey);
                if (!model) {
                  return null;
                }
                return (
                  <ModelListRow
                    key={modelKey}
                    index={index}
                    slug={model.slug}
                    name={model.name}
                    shortName={model.shortName}
                    subProvider={model.subProvider}
                    instanceId={model.instanceId}
                    driverKind={model.driverKind}
                    providerDisplayName={model.instanceDisplayName}
                    isFavorite={favoritesSet.has(providerModelKey(model.instanceId, model.slug))}
                    isSelected={modelKey === activeModelKey}
                    showSelection={false}
                    showProvider
                    preferShortName
                    useTriggerLabel={false}
                    showNewBadge={model.badge === "new"}
                    unavailable={model.isUnavailable === true}
                    jumpLabel={modelJumpLabelByKey.get(modelKey) ?? null}
                    onToggleFavorite={props.onToggleFavorite}
                  />
                );
              }}
            />
            <ComboboxEmpty className="empty:h-0">No models found</ComboboxEmpty>
            {providerNotices.map(({ instanceId, notice }) => (
              <p
                key={instanceId}
                className="shrink-0 border-t border-border/70 px-3 py-2 text-xs leading-snug text-muted-foreground"
              >
                {notice}
              </p>
            ))}
          </div>
        </Combobox>
      </div>
    </TooltipProvider>
  );
});
