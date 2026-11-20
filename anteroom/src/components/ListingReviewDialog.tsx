import type { ReactNode } from "react";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "./ui/dialog";

/** A listing review modal (history, regenerate, price drop) on T3's dialog. */
export function ListingReviewDialog({ title, onClose, children, actions, busy = false, className }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  busy?: boolean;
  className?: string;
}) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogPopup className={className ?? "max-w-2xl"} showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogPanel className="text-sm">{children}</DialogPanel>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>Close</Button>
          {actions}
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}
