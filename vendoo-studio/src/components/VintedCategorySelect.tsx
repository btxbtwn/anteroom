import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { CategoryTreeSelect } from "./ListingFormControls";
import type { CategoryChoice } from "./categoryTree";

/** Vinted's own category tree, read through the signed-in Chrome session. */
export function VintedCategorySelect({ convId, id, fieldKey, value, disabled, onChange }: {
  convId: string; id: string; fieldKey: string; value: string;
  disabled?: boolean; onChange: (value: string) => void;
}) {
  const categories = useQuery({
    queryKey: ["vinted-categories", convId],
    queryFn: () => api.nativeMarketplaces.vintedCategories(convId),
    staleTime: 6 * 60 * 60 * 1000,
    retry: false,
  });
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
      {value && <p className="text-xs text-muted-foreground">Saved category: {value}</p>}
    </div>;
  }
  return <CategoryTreeSelect key={value} id={id} fieldKey={fieldKey} value={value} choices={choices}
    disabled={disabled} onChange={(choice) => onChange(choice.label)} />;
}
