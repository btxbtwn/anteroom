/**
 * Reorder photo tiles by dragging them with a mouse, a trackpad or a finger.
 * Touch works like iOS Photos and the marketplaces' own apps: press and hold a
 * photo, then drag it. A quick swipe still scrolls the row. The photo viewer's
 * Earlier and Later buttons are the way to reorder without dragging.
 */
import * as React from "react";
import { closestCenter, DndContext, MouseSensor, TouchSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { horizontalListSortingStrategy, SortableContext, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

/** How long a finger rests on a photo before it lifts, as with a long press on iOS. */
export const TOUCH_HOLD_MS = 250;

export function SortablePhotos({ ids, onMove, children }: {
  ids: readonly string[];
  onMove: (from: number, to: number) => void;
  children: React.ReactNode;
}) {
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: TOUCH_HOLD_MS, tolerance: 8 } }),
  );
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    ignoreClickAfterDrag();
    if (!over || active.id === over.id) return;
    onMove(ids.indexOf(String(active.id)), ids.indexOf(String(over.id)));
  };
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd} onDragCancel={ignoreClickAfterDrag}>
      <SortableContext items={[...ids]} strategy={horizontalListSortingStrategy}>{children}</SortableContext>
    </DndContext>
  );
}

/** One draggable tile. `children` gets whether it is the one being dragged. */
export function SortablePhoto({ id, disabled, children }: {
  id: string;
  disabled?: boolean;
  children: (dragging: boolean) => React.ReactNode;
}) {
  const { setNodeRef, listeners, transform, transition, isDragging } = useSortable({ id, disabled });
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      data-sortable-photo={id}
      style={{ transform: CSS.Translate.toString(transform), transition, zIndex: isDragging ? 1 : undefined }}
      // No iOS link callout or text selection on a long press.
      className="relative shrink-0 touch-manipulation select-none [-webkit-touch-callout:none]"
    >
      {children(isDragging)}
    </div>
  );
}

/** Letting go of a dragged photo must not also open it. */
function ignoreClickAfterDrag() {
  const swallow = (event: MouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
  };
  window.addEventListener("click", swallow, { capture: true, once: true });
  window.setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
}
