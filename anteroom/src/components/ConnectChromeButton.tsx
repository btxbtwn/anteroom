import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { toastManager } from "./ui/toast";

const WAIT_FOR_EXTENSION_MS = 25_000;
const WAIT_POLL_MS = 500;

async function waitForExtensionConnected(
  queryClient: ReturnType<typeof useQueryClient>,
  timeoutMs = WAIT_FOR_EXTENSION_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await queryClient.fetchQuery({
      queryKey: ["extension-status"],
      queryFn: api.extension.status,
      staleTime: 0,
    });
    if (status?.connected) return true;
    await new Promise((resolve) => setTimeout(resolve, WAIT_POLL_MS));
  }
  const finalStatus = await queryClient.fetchQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    staleTime: 0,
  });
  return Boolean(finalStatus?.connected);
}

export function ConnectChromeButton({
  compact = false,
  variant = "outline",
  className,
}: {
  compact?: boolean;
  variant?: "default" | "outline";
  className?: string;
}) {
  const queryClient = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const connect = useMutation({
    mutationFn: api.desktop.connectChrome,
    onSuccess: async (result) => {
      queryClient.invalidateQueries({ queryKey: ["extension-status"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
      if (result?.connected) return;
      setWaiting(true);
      try {
        await waitForExtensionConnected(queryClient);
      } finally {
        setWaiting(false);
        queryClient.invalidateQueries({ queryKey: ["extension-status"] });
        queryClient.invalidateQueries({ queryKey: ["status"] });
      }
    },
    onError: (err: Error) => {
      setWaiting(false);
      toastManager.add({ type: "error", title: "Could not open Chrome", description: err.message });
    },
  });

  const busy = connect.isPending || waiting;
  let label = "Connect Chrome";
  if (connect.isPending) label = "Opening Chrome…";
  else if (waiting) label = "Waiting for extension…";

  return (
    <Button
      type="button"
      variant={variant}
      size={compact ? "micro" : "sm"}
      className={className}
      disabled={busy}
      onClick={() => connect.mutate()}
    >
      {busy ? <Spinner /> : null}
      {label}
    </Button>
  );
}
