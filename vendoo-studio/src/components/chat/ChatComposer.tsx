/**
 * The composer shared by the listing chat and the business assistant, with
 * T3 Code's surface, body and footer markup (components/chat/ChatComposer.tsx,
 * MIT, © 2026 T3 Tools Inc.): the prompt editor, attached image chips, then
 * the model and effort pickers on the left and attach / send / stop on the right.
 */
import { useState, type ReactNode, type RefObject } from "react";
import { cn } from "../../lib/utils";
import { ComposerPromptEditor, type ComposerPromptEditorHandle } from "../ComposerPromptEditor";
import { ComposerAttachButton, ComposerImageChips, type useComposerImages } from "./ComposerAttachments";
import { ComposerPrimaryActions } from "./ComposerPrimaryActions";
import { ComposerSurface } from "./ComposerSurface";
import { composerImageMimeType } from "./composerImages";
import { ComposerUsageLimits, isUsageLimitsCommand } from "./ComposerUsageLimits";
import { ProviderModelPicker } from "./ProviderModelPicker";
import { CompactComposerControlsMenu } from "./TraitsPicker";
import { useComposerModel, useModelFavorites } from "./useComposerModel";

/**
 * The model picker and the "…" effort menu, saving the same settings
 * Settings → Providers edits.
 */
function ComposerModelControls({ disabled }: { disabled: boolean }) {
  const model = useComposerModel();
  const { favorites, toggle } = useModelFavorites();
  if (model.loading || !model.active) return null;
  return (
    <>
      <ProviderModelPicker
        activeInstanceId={model.active}
        model={model.model}
        instanceEntries={model.entries}
        modelOptionsByInstance={model.modelOptionsByInstance}
        favorites={favorites}
        onToggleFavorite={toggle}
        disabled={disabled || model.pending}
        triggerClassName="-ms-2.5"
        onInstanceModelChange={model.selectModel}
      />
      {model.effort ? (
        <CompactComposerControlsMenu
          effort={model.effort}
          onEffortChange={model.selectEffort}
          disabled={disabled || model.pending}
        />
      ) : null}
      {model.error ? <span className="truncate text-destructive-foreground text-xs" role="alert">{model.error}</span> : null}
    </>
  );
}

function dragHasFiles(transfer: DataTransfer | null): boolean {
  return Boolean(transfer && Array.from(transfer.types).includes("Files"));
}

interface Props {
  composerRef: RefObject<ComposerPromptEditorHandle | null>;
  input: string;
  onInputChange: (value: string) => void;
  placeholder: string;
  images: ReturnType<typeof useComposerImages>;
  /** Shown above the prompt inside the surface (fields picked in the browser). */
  header?: ReactNode;
  busy: boolean;
  stopping?: boolean;
  /** Something besides typed text can be sent (picked browser fields). */
  hasOtherContent?: boolean;
  onSend: () => void;
  onStop?: () => void;
  stopLabel?: string;
  /** While busy a follow-up is queued; null when this chat cannot queue. */
  queueLabel?: string | null;
  className?: string;
}

export function ChatComposer({
  composerRef,
  input,
  onInputChange,
  placeholder,
  images,
  header,
  busy,
  stopping = false,
  hasOtherContent = false,
  onSend,
  onStop,
  stopLabel,
  queueLabel,
  className,
}: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  // `/usage-limits` is answered here and never sent, as in T3 Code.
  const submit = () => {
    if (isUsageLimitsCommand(input)) {
      onInputChange("");
      setUsageOpen(true);
      return;
    }
    setUsageOpen(false);
    onSend();
  };
  const canSubmit = Boolean(input.trim() || images.images.length || hasOtherContent);
  const addImages = (files: File[]) => {
    if (images.addFiles(files)) composerRef.current?.focus();
  };
  return (
    <div className={cn("chat-composer shrink-0 px-3 pb-3 pt-1.5 sm:px-4 sm:pb-4", className)}>
      <ComposerSurface.Shell>
        <ComposerSurface.Host>
          <ComposerSurface.Main>
            <div
              data-chat-composer-surface="true"
              className={cn(
                "rounded-3xl transition-[background-color] duration-200",
                dragOver ? "bg-accent/45 ring-1 ring-inset ring-primary/70" : null,
              )}
              onDragOver={(event) => {
                if (!dragHasFiles(event.dataTransfer)) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
                setDragOver(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOver(false);
              }}
              onDrop={(event) => {
                setDragOver(false);
                if (!dragHasFiles(event.dataTransfer)) return;
                // Claimed here: the listing panel's photo drop skips a handled drop.
                event.preventDefault();
                addImages(Array.from(event.dataTransfer.files));
              }}
            >
              <div data-chat-composer-body="true" className="relative px-3 pb-2 pt-3.5 sm:px-4 sm:pt-4">
                {usageOpen ? <ComposerUsageLimits onDismiss={() => setUsageOpen(false)} /> : null}
                {header}
                <ComposerImageChips images={images.images} onRemove={images.remove} />
                <ComposerPromptEditor
                  handleRef={composerRef}
                  value={input}
                  onChange={onInputChange}
                  onSubmit={submit}
                  placeholder={placeholder}
                  onPasteFiles={(files) => {
                    const pasted = files.filter((file) => composerImageMimeType(file));
                    if (!pasted.length) return false;
                    addImages(pasted);
                    return true;
                  }}
                />
              </div>
              {images.error ? (
                <p role="alert" className="px-4 pb-1 text-destructive-foreground text-xs">{images.error}</p>
              ) : null}
              <div
                data-chat-composer-footer="true"
                className="flex min-w-0 flex-nowrap items-center justify-between gap-2 overflow-visible px-3 pb-3 sm:px-4 sm:pb-4"
              >
                <div
                  data-chat-composer-controls="left"
                  className="relative -m-1 -ms-3.5 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto p-1 ps-3.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                >
                  <ComposerModelControls disabled={busy} />
                </div>
                <div data-chat-composer-actions="right" className="flex shrink-0 flex-nowrap items-center justify-end gap-2">
                  <ComposerAttachButton onFiles={addImages} />
                  <ComposerPrimaryActions
                    busy={busy}
                    stopping={stopping}
                    canSubmit={canSubmit}
                    onSend={submit}
                    onStop={onStop}
                    stopLabel={stopLabel}
                    queueLabel={queueLabel}
                  />
                </div>
              </div>
            </div>
          </ComposerSurface.Main>
        </ComposerSurface.Host>
      </ComposerSurface.Shell>
    </div>
  );
}
