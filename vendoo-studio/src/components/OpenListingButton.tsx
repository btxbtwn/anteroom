import type { MouseEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";
import { api } from "../api/client";
import { Button, type ButtonSize, type ButtonVariant } from "./ui/button";
import { toastManager } from "./ui/toast";

function stopWindowDrag(event: MouseEvent) {
  event.stopPropagation();
}

export function OpenListingButton({
  jobId,
  vendooItemId,
  vendooUrl,
  variant = "outline",
  size = "xs",
  className,
}: {
  jobId: string;
  vendooItemId?: string | null;
  vendooUrl?: string | null;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}) {
  const open = useMutation({
    mutationFn: () => api.jobs.open(jobId),
    onError: (err: Error) => {
      toastManager.add({
        type: "error",
        title: "Could not open listing",
        description: err.message || "Could not open the listing in Vendoo.",
      });
    },
  });

  if (!vendooItemId && !vendooUrl) return null;

  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      disabled={open.isPending}
      title="Open this listing in Vendoo"
      aria-label="Open listing in Vendoo"
      onMouseDown={stopWindowDrag}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        open.mutate();
      }}
    >
      <ExternalLinkIcon />
      {open.isPending ? "Opening…" : "Open listing"}
    </Button>
  );
}
