import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../api/client";
import { useWorkspaceRuntime } from "../app/runtime";

const SELLWILD_SIGN_IN = "https://sellwild.com/";

/** Account choices are read by Anteroom; signing in never creates a listing. On the web they come from the seller's own Chrome. */
export function SellwildAccountChoices({ convId, disabled, connected }: { convId: string; disabled?: boolean; connected: boolean }) {
  const { hosted } = useWorkspaceRuntime();
  const client = useQueryClient();
  const signIn = useMutation({ mutationFn: () => api.nativeMarketplaces.sellwildConnect(convId) });
  const formKey = ["native-editor-form", convId, "sellwild"];
  useEffect(() => {
    if (!signIn.isSuccess || connected || disabled) return;
    const queryKey = ["native-editor-form", convId, "sellwild"];
    const timer = setInterval(() => {
      if (!client.isFetching({ queryKey })) void client.invalidateQueries({ queryKey });
    }, 3000);
    return () => clearInterval(timer);
  }, [client, convId, signIn.isSuccess, connected, disabled]);
  if (hosted) {
    return <div className="flex flex-col gap-2 border-b py-3 text-xs">
      <p className="text-muted-foreground">Your Sellwild address, discounts, and default package use your Sellwild sign-in in Chrome.</p>
      {connected ? <p role="status">Sellwild is signed in.</p>
        : <a href={SELLWILD_SIGN_IN} target="_blank" rel="noreferrer" className="text-primary underline">Sign in to Sellwild in Chrome</a>}
      {!connected && <button type="button" className="text-left text-primary underline" disabled={disabled}
        onClick={() => void client.invalidateQueries({ queryKey: formKey })}>Reload Sellwild choices</button>}
    </div>;
  }
  return <div className="flex flex-col gap-2 border-b py-3 text-xs">
    <p className="text-muted-foreground">Your Sellwild address, discounts, and default package use your Sellwild sign-in in Anteroom.</p>
    {connected ? <p role="status">Sellwild is signed in.</p> : <button type="button" className="text-left text-primary underline" disabled={disabled || signIn.isPending}
      onClick={() => signIn.mutate()}>Sign in to Sellwild in Anteroom</button>}
    {signIn.isSuccess && !connected && <p className="text-muted-foreground">Finish signing in in the Sellwild window. Your choices will reload automatically.</p>}
    {signIn.isError && <p role="alert" className="text-warning-foreground">{signIn.error.message}</p>}
  </div>;
}
