import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

/** Account choices are read by Anteroom; signing in never creates a listing. */
export function SellwildAccountChoices({ convId, disabled }: { convId: string; disabled?: boolean }) {
  const client = useQueryClient();
  const signIn = useMutation({ mutationFn: () => api.nativeMarketplaces.sellwildConnect(convId) });
  const reload = useMutation({ mutationFn: () => client.invalidateQueries({ queryKey: ["native-editor-form", convId, "sellwild"] }) });
  return <div className="flex flex-col gap-2 border-b py-3 text-xs">
    <p className="text-muted-foreground">Your Sellwild address, discounts, and default package use your Sellwild sign-in in Anteroom.</p>
    <button type="button" className="text-left text-primary underline" disabled={disabled || signIn.isPending}
      onClick={() => signIn.mutate()}>Sign in to Sellwild in Anteroom</button>
    {signIn.isSuccess && <p className="text-muted-foreground">Finish signing in in the Sellwild window, then reload choices. This private sign-in lasts while Anteroom is open.</p>}
    {signIn.isError && <p role="alert" className="text-warning-foreground">{signIn.error.message}</p>}
    <button type="button" className="text-left text-primary underline" disabled={disabled || reload.isPending}
      onClick={() => reload.mutate()}>Reload Sellwild choices</button>
  </div>;
}
