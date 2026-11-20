// Adapted from T3 Code's ConfirmDialogHost (https://github.com/pingdotgg/t3code),
// MIT License, (c) 2026 T3 Tools Inc.
import { useEffect, useSyncExternalStore } from "react";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  resolveConfirmDialogCopy,
  respondToConfirmDialog,
  subscribeConfirmDialog,
} from "../ui/confirmDialog";
import {
  AlertDialog,
  AlertDialogClose,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogPopup,
  AlertDialogTitle,
} from "./ui/alert-dialog";
import { Button } from "./ui/button";

export function ConfirmDialogHost() {
  const state = useSyncExternalStore(subscribeConfirmDialog, readConfirmDialogState, readConfirmDialogState);

  useEffect(() => registerConfirmDialogHost(), []);

  const copy = resolveConfirmDialogCopy(state.status === "idle" ? "" : state.message);
  const variant = state.status === "idle" ? "default" : state.variant;
  const confirmLabel = state.status === "idle" ? "Confirm" : state.confirmLabel;

  return (
    <AlertDialog
      open={state.status === "confirming"}
      onOpenChange={(open) => {
        if (!open) respondToConfirmDialog(false);
      }}
      onOpenChangeComplete={(open) => {
        if (!open) completeConfirmDialogClose();
      }}
    >
      <AlertDialogPopup>
        <AlertDialogHeader>
          <AlertDialogTitle>{copy.title}</AlertDialogTitle>
          {copy.description ? (
            <AlertDialogDescription className="whitespace-pre-line">{copy.description}</AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogClose render={<Button variant="outline" />}>Cancel</AlertDialogClose>
          <Button variant={variant} onClick={() => respondToConfirmDialog(true)}>
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogPopup>
    </AlertDialog>
  );
}
