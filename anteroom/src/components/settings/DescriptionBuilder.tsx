import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  pointerWithin,
  rectIntersection,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragMoveEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowDownIcon, ArrowUpIcon, GripVerticalIcon, PlusIcon, XIcon } from "lucide-react";
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DescriptionFormulaBlock } from "../../api/client";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

const ACTIVE_DROPZONE_ID = "__description-active__";
const AVAILABLE_DROPZONE_ID = "__description-available__";

/** Compile the formula preview in the seller's exact attribute order. */
export function descriptionPreview(
  blocks: readonly Pick<DescriptionFormulaBlock, "id" | "snippet">[],
  ids: readonly string[],
  empty: string,
): string {
  const byId = new Map(blocks.map((block) => [block.id, block.snippet]));
  const parts = ids.map((id) => byId.get(id)).filter((snippet): snippet is string => Boolean(snippet));
  return parts.length ? parts.join("\n\n") : empty;
}

/** Move an active attribute from `fromIndex` to `toIndex`. */
export function reorderBlockIds(ids: readonly string[], fromIndex: number, toIndex: number): string[] {
  if (
    fromIndex === toIndex ||
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= ids.length ||
    toIndex >= ids.length
  ) {
    return [...ids];
  }
  return arrayMove([...ids], fromIndex, toIndex);
}

/** Insert an attribute at `index` (or at the end), keeping each attribute unique. */
export function insertBlockId(ids: readonly string[], id: string, index?: number): string[] {
  const without = ids.filter((item) => item !== id);
  const clamped =
    index === undefined ? without.length : Math.max(0, Math.min(index, without.length));
  return [...without.slice(0, clamped), id, ...without.slice(clamped)];
}

/** Remove an attribute from the active description list. */
export function removeBlockId(ids: readonly string[], id: string): string[] {
  return ids.filter((item) => item !== id);
}

function computeInsertIndex(
  event: Pick<DragMoveEvent, "active" | "over">,
  activeIds: readonly string[],
): number | null {
  const { over, active } = event;
  if (!over) return null;
  const overId = String(over.id);
  if (overId === AVAILABLE_DROPZONE_ID) return null;
  if (overId === ACTIVE_DROPZONE_ID) return activeIds.length;
  const overIndex = activeIds.indexOf(overId);
  if (overIndex === -1) return null;
  const translated = active.rect.current.translated;
  if (!translated) return overIndex;
  const activeMidY = translated.top + translated.height / 2;
  const overMidY = over.rect.top + over.rect.height / 2;
  return activeMidY > overMidY ? overIndex + 1 : overIndex;
}

function InsertionLine() {
  return (
    <div
      aria-hidden
      data-slot="description-drop-indicator"
      className="pointer-events-none mx-3 my-0.5 h-0.5 rounded-full bg-foreground"
    />
  );
}

function SortableActiveRow({
  block,
  index,
  total,
  disabled,
  onMove,
  onRemove,
}: {
  block: DescriptionFormulaBlock;
  index: number;
  total: number;
  disabled: boolean;
  onMove: (from: number, to: number) => void;
  onRemove: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: block.id,
    disabled,
  });

  return (
    <div
      ref={setNodeRef}
      data-slot="description-active-block"
      data-block-id={block.id}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "flex min-h-11 min-w-0 items-center gap-2 bg-background px-2.5 py-2 select-none dark:bg-card",
        isDragging && "z-10 opacity-40",
      )}
    >
      <div
        className="flex min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing"
        {...listeners}
      >
        <button
          type="button"
          disabled={disabled}
          aria-label={`Drag to reorder ${block.label}`}
          data-focus-control="reorder"
          className="inline-flex size-8 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing disabled:pointer-events-none disabled:opacity-50 sm:size-7"
          {...attributes}
        >
          <GripVerticalIcon className="size-4" aria-hidden />
        </button>
        <span
          aria-hidden
          className="hidden w-4 shrink-0 text-center text-2xs tabular-nums text-muted-foreground sm:inline"
        >
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-foreground">{block.label}</span>
          <span className="block text-xs text-muted-foreground">{block.detail}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <Button
          size="icon-xs"
          variant="ghost-muted"
          disabled={disabled || index === 0}
          aria-label={`Move ${block.label} up`}
          onClick={() => onMove(index, index - 1)}
        >
          <ArrowUpIcon aria-hidden />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost-muted"
          disabled={disabled || index === total - 1}
          aria-label={`Move ${block.label} down`}
          onClick={() => onMove(index, index + 1)}
        >
          <ArrowDownIcon aria-hidden />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost-muted"
          disabled={disabled}
          aria-label={`Remove ${block.label}`}
          data-focus-control="remove"
          onClick={() => onRemove(block.id)}
        >
          <XIcon aria-hidden />
        </Button>
      </div>
    </div>
  );
}

function DraggableAvailableCard({
  block,
  disabled,
  onAdd,
}: {
  block: DescriptionFormulaBlock;
  disabled: boolean;
  onAdd: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: block.id,
    disabled,
  });

  return (
    <div
      ref={setNodeRef}
      data-slot="description-available-block"
      data-block-id={block.id}
      className={cn(
        "flex min-h-11 min-w-0 items-center gap-2 bg-background px-2.5 py-2 select-none transition-colors hover:bg-muted/40 dark:bg-card",
        isDragging && "opacity-40",
      )}
    >
      <div
        className="flex min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing"
        {...listeners}
      >
        <button
          type="button"
          disabled={disabled}
          aria-label={`Drag ${block.label} into description`}
          className="inline-flex size-8 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground/75 outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing disabled:pointer-events-none disabled:opacity-50 sm:size-7"
          {...attributes}
        >
          <GripVerticalIcon className="size-4" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">{block.label}</span>
          <span className="block truncate text-xs text-muted-foreground">{block.detail}</span>
        </div>
      </div>
      <Button
        size="icon-xs"
        variant="ghost-muted"
        disabled={disabled}
        aria-label={`Add ${block.label}`}
        data-focus-control="add"
        onClick={() => onAdd(block.id)}
      >
        <PlusIcon aria-hidden />
      </Button>
    </div>
  );
}

function DragGhost({ block }: { block: DescriptionFormulaBlock }) {
  return (
    <div className="flex min-h-11 items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-sm">
      <GripVerticalIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{block.label}</span>
        <span className="block truncate text-xs text-muted-foreground">{block.detail}</span>
      </div>
    </div>
  );
}

function ActiveDropZone({
  blocks,
  blockIds,
  disabled,
  dragOrigin,
  insertIndex,
  onMove,
  onRemove,
}: {
  blocks: readonly DescriptionFormulaBlock[];
  blockIds: readonly string[];
  disabled: boolean;
  dragOrigin: "active" | "available" | null;
  insertIndex: number | null;
  onMove: (from: number, to: number) => void;
  onRemove: (id: string) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: ACTIVE_DROPZONE_ID, disabled });
  const highlight = dragOrigin === "available" && (isOver || insertIndex !== null);

  return (
    <div
      ref={setNodeRef}
      data-slot="description-active-zone"
      role="region"
      aria-label="In your description"
      className={cn(
        "overflow-hidden rounded-lg border border-border/60 bg-background transition-colors dark:bg-card",
        highlight && "border-foreground/40 bg-accent/35",
      )}
    >
      <SortableContext items={[...blockIds]} strategy={verticalListSortingStrategy}>
        {blocks.length === 0 ? (
          <div className="flex min-h-20 items-center justify-center px-4 py-4 text-center text-xs text-muted-foreground">
            {highlight
              ? "Drop to add this attribute."
              : "Drag attributes here or tap + to add. With none added, listings use one short factual sentence."}
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {blocks.map((block, index) => (
              <div key={block.id}>
                {dragOrigin === "available" && insertIndex === index ? <InsertionLine /> : null}
                <SortableActiveRow
                  block={block}
                  index={index}
                  total={blocks.length}
                  disabled={disabled}
                  onMove={onMove}
                  onRemove={onRemove}
                />
              </div>
            ))}
            {dragOrigin === "available" && insertIndex === blocks.length ? <InsertionLine /> : null}
          </div>
        )}
      </SortableContext>
    </div>
  );
}

function AvailableDropZone({
  blocks,
  disabled,
  dragOrigin,
  overAvailable,
  onAdd,
}: {
  blocks: readonly DescriptionFormulaBlock[];
  disabled: boolean;
  dragOrigin: "active" | "available" | null;
  overAvailable: boolean;
  onAdd: (id: string) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: AVAILABLE_DROPZONE_ID, disabled });
  const highlight = dragOrigin === "active" && (isOver || overAvailable);

  return (
    <div className="grid min-w-0 gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">Available attributes</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {highlight ? "Drop here to remove" : `${blocks.length} available`}
        </span>
      </div>
      <div
        ref={setNodeRef}
        data-slot="description-available-zone"
        role="region"
        aria-label="Available attributes"
        className={cn(
          "min-w-0 overflow-hidden rounded-lg border border-border/60 transition-colors",
          highlight && "border-foreground/40 bg-accent/35",
        )}
      >
        {blocks.length === 0 ? (
          <div className="flex min-h-14 items-center justify-center bg-background px-4 py-3 text-center text-xs text-muted-foreground dark:bg-card">
            {highlight
              ? "Drop here to remove from your description."
              : "All attributes are in your description. Drag one here to remove it."}
          </div>
        ) : (
          <div className="grid min-w-0 grid-cols-1 gap-px bg-border/50 sm:grid-cols-2">
            {blocks.map((block) => (
              <DraggableAvailableCard
                key={block.id}
                block={block}
                disabled={disabled}
                onAdd={onAdd}
              />
            ))}
            {blocks.length % 2 === 1 ? (
              <span aria-hidden className="hidden bg-background sm:block dark:bg-card" />
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export function DescriptionBuilder({
  blocks,
  blockIds,
  emptyDescription,
  showPreview = true,
  disabled = false,
  onChange,
}: {
  blocks: readonly DescriptionFormulaBlock[];
  blockIds: readonly string[];
  emptyDescription: string;
  showPreview?: boolean;
  disabled?: boolean;
  onChange: (nextIds: string[]) => void;
}) {
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragOrigin, setDragOrigin] = useState<"active" | "available" | null>(null);
  const [insertIndex, setInsertIndex] = useState<number | null>(null);
  const [overAvailable, setOverAvailable] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const builderRef = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<{ control: "add" | "remove" | "reorder"; id?: string } | null>(null);

  // Button actions replace their source control in the parent's next render.
  useLayoutEffect(() => {
    const target = pendingFocus.current;
    pendingFocus.current = null;
    if (!target || !builderRef.current) return;
    const controls = builderRef.current.querySelectorAll<HTMLButtonElement>(
      `[data-focus-control="${target.control}"]`,
    );
    Array.from(controls).find((button) =>
      !button.disabled &&
      (target.id === undefined || button.closest<HTMLElement>("[data-block-id]")?.dataset.blockId === target.id),
    )?.focus();
  }, [blockIds]);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 120, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const byId = useMemo(() => new Map(blocks.map((block) => [block.id, block])), [blocks]);
  const activeSet = useMemo(() => new Set(blockIds), [blockIds]);
  const activeBlocks = useMemo(
    () =>
      blockIds
        .map((id) => byId.get(id))
        .filter((block): block is DescriptionFormulaBlock => Boolean(block)),
    [blockIds, byId],
  );
  const availableBlocks = useMemo(
    () => blocks.filter((block) => !activeSet.has(block.id)),
    [blocks, activeSet],
  );
  const draggingBlock = draggingId ? byId.get(draggingId) ?? null : null;

  const collisionDetection = useCallback<CollisionDetection>(
    (args) => {
      const pointer = pointerWithin(args);
      if (pointer.length > 0) {
        const itemHit = pointer.find(
          (hit) => hit.id !== ACTIVE_DROPZONE_ID && hit.id !== AVAILABLE_DROPZONE_ID,
        );
        return itemHit ? [itemHit] : pointer;
      }
      if (dragOrigin === "available") {
        const intersecting = rectIntersection(args).filter(
          (hit) => hit.id !== AVAILABLE_DROPZONE_ID,
        );
        const itemHit = intersecting.find((hit) => hit.id !== ACTIVE_DROPZONE_ID);
        return itemHit ? [itemHit] : intersecting;
      }
      const activeOnly = args.droppableContainers.filter(
        (container) => container.id !== AVAILABLE_DROPZONE_ID && container.id !== ACTIVE_DROPZONE_ID,
      );
      return closestCenter({ ...args, droppableContainers: activeOnly });
    },
    [dragOrigin],
  );

  const handleAdd = (id: string) => {
    const label = byId.get(id)?.label ?? id;
    pendingFocus.current = { control: "reorder", id };
    onChange(insertBlockId(blockIds, id));
    setAnnouncement(`Added ${label} to description.`);
  };

  const handleRemove = (id: string) => {
    const label = byId.get(id)?.label ?? id;
    const index = activeBlocks.findIndex((block) => block.id === id);
    const neighbor = activeBlocks[index + 1] ?? activeBlocks[index - 1];
    pendingFocus.current = neighbor
      ? { control: "remove", id: neighbor.id }
      : { control: "add", id };
    onChange(removeBlockId(blockIds, id));
    setAnnouncement(`Removed ${label} from description.`);
  };

  const handleMove = (from: number, to: number) => {
    const id = blockIds[from];
    if (!id) return;
    const label = byId.get(id)?.label ?? id;
    onChange(reorderBlockIds(blockIds, from, to));
    setAnnouncement(`Moved ${label} to position ${to + 1}.`);
  };

  const handleClear = () => {
    pendingFocus.current = { control: "add" };
    onChange([]);
    setAnnouncement("Cleared all description attributes.");
  };

  const resetDrag = () => {
    setDraggingId(null);
    setDragOrigin(null);
    setInsertIndex(null);
    setOverAvailable(false);
  };

  const handleDragStart = (event: DragStartEvent) => {
    const id = String(event.active.id);
    setDraggingId(id);
    setDragOrigin(activeSet.has(id) ? "active" : "available");
    setInsertIndex(null);
    setOverAvailable(false);
  };

  const updateDragIndicator = (event: DragMoveEvent) => {
    const { over } = event;
    if (!over || !draggingId) {
      setInsertIndex(null);
      setOverAvailable(false);
      return;
    }
    const overId = String(over.id);
    if (dragOrigin === "available") {
      setOverAvailable(false);
      setInsertIndex(computeInsertIndex(event, blockIds));
      return;
    }
    if (dragOrigin === "active") {
      setInsertIndex(null);
      setOverAvailable(overId === AVAILABLE_DROPZONE_ID);
    }
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    const id = String(active.id);
    const label = byId.get(id)?.label ?? id;

    if (over && dragOrigin === "available") {
      const targetIndex = computeInsertIndex(event, blockIds);
      if (targetIndex !== null) {
        onChange(insertBlockId(blockIds, id, targetIndex));
        setAnnouncement(`Added ${label} at position ${targetIndex + 1}.`);
      }
    } else if (over && dragOrigin === "active") {
      const overId = String(over.id);
      if (overId === AVAILABLE_DROPZONE_ID) {
        onChange(removeBlockId(blockIds, id));
        setAnnouncement(`Removed ${label} from description.`);
      } else {
        const oldIndex = blockIds.indexOf(id);
        const newIndex = blockIds.indexOf(overId);
        if (oldIndex !== -1 && newIndex !== -1 && oldIndex !== newIndex) {
          onChange(reorderBlockIds(blockIds, oldIndex, newIndex));
          setAnnouncement(`Moved ${label} to position ${newIndex + 1}.`);
        }
      }
    }
    resetDrag();
  };

  return (
    <div ref={builderRef} className="grid min-w-0 gap-3">
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={handleDragStart}
        onDragOver={updateDragIndicator}
        onDragMove={dragOrigin === "available" ? updateDragIndicator : undefined}
        onDragEnd={handleDragEnd}
        onDragCancel={resetDrag}
      >
        <div className="grid min-w-0 gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">In your description</span>
            <div className="flex items-center gap-2">
              <span className="text-xs tabular-nums text-muted-foreground">
                {activeBlocks.length} {activeBlocks.length === 1 ? "section" : "sections"}
              </span>
              {activeBlocks.length > 0 ? (
                <Button size="xs" variant="ghost-muted" disabled={disabled} onClick={handleClear}>
                  Clear
                </Button>
              ) : null}
            </div>
          </div>
          <ActiveDropZone
            blocks={activeBlocks}
            blockIds={blockIds}
            disabled={disabled}
            dragOrigin={dragOrigin}
            insertIndex={insertIndex}
            onMove={handleMove}
            onRemove={handleRemove}
          />
        </div>

        <AvailableDropZone
          blocks={availableBlocks}
          disabled={disabled}
          dragOrigin={dragOrigin}
          overAvailable={overAvailable}
          onAdd={handleAdd}
        />

        <DragOverlay dropAnimation={null}>
          {draggingBlock ? <DragGhost block={draggingBlock} /> : null}
        </DragOverlay>
      </DndContext>

      {showPreview ? (
        <div className="grid min-w-0 gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">Preview</span>
          <pre
            data-slot="description-formula-preview"
            className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-border/60 bg-muted/40 p-3 font-mono text-xs text-foreground"
          >
            {descriptionPreview(blocks, blockIds, emptyDescription)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
