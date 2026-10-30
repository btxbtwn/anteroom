import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { CategoryTreeSelect } from "./ListingFormControls";
import type { CategoryChoice } from "./categoryTree";

/** Vinted's own category tree, read through Anteroom’s own private sign-in window. */
export function VintedCategorySelect({ convId, id, fieldKey, value, disabled, onChange }: {
  convId: string; id: string; fieldKey: string; value: string;
  disabled?: boolean; onChange: (value: string) => void;
}) {
  const queryClient = useQueryClient();
  const signIn = useMutation({ mutationFn: () => api.nativeMarketplaces.vintedConnect(convId) });
  const categories = useQuery({
    queryKey: ["vinted-categories", convId],
    queryFn: () => api.nativeMarketplaces.vintedCategories(convId),
    staleTime: 6 * 60 * 60 * 1000,
    retry: false,
    refetchInterval: (query) => signIn.isSuccess && query.state.status === "error" && !disabled ? 3000 : false,
  });
  React.useEffect(() => {
    if (categories.isSuccess) void queryClient.invalidateQueries({ queryKey: ["vinted-brands", convId] });
  }, [categories.isSuccess, convId, queryClient]);
  const choices = React.useMemo<CategoryChoice[]>(
    () => (categories.data?.options ?? [])
      .filter((label) => typeof label === "string" && label.trim())
      .map((label) => ({ label, id: label })),
    [categories.data],
  );
  if (categories.isPending) {
    return <p role="status" className="text-xs text-muted-foreground">Loading Vinted categories…</p>;
  }
  if (categories.isError) {
    return <div className="flex min-w-0 flex-col gap-2">
      <p role="alert" className="text-xs text-warning-foreground">{categories.error.message}</p>
      <button type="button" className="text-left text-xs text-primary underline" disabled={disabled || signIn.isPending}
        onClick={() => signIn.mutate()}>Sign in to Vinted in Anteroom</button>
      {signIn.isSuccess && <p className="text-xs text-muted-foreground">Finish signing in in the Vinted window. Your choices will reload automatically.</p>}
      {signIn.isError && <p role="alert" className="text-xs text-warning-foreground">{signIn.error.message}</p>}
      <button type="button" className="text-left text-xs text-primary underline" disabled={disabled || categories.isFetching}
        onClick={async () => {
          const result = await categories.refetch();
          if (result.isSuccess) void queryClient.invalidateQueries({ queryKey: ["vinted-brands", convId] });
        }}>Reload Vinted choices</button>
      {value && <p className="text-xs text-muted-foreground">Saved category: {value}</p>}
    </div>;
  }
  return <CategoryTreeSelect key={value} id={id} fieldKey={fieldKey} value={value} choices={choices}
    disabled={disabled} onChange={(choice) => onChange(choice.label)} />;
}
