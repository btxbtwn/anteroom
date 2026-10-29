import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { SearchableSelect } from "./ListingFormControls";

export function VintedBrandSelect({ convId, category, id, fieldKey, value, disabled, onChange }: {
  convId: string; category: string; id: string; fieldKey: string; value: string;
  disabled?: boolean; onChange: (value: string) => void;
}) {
  const [query, setQuery] = React.useState(value);
  const [search, setSearch] = React.useState(value);
  React.useEffect(() => { setQuery(value); }, [value]);
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query), 300);
    return () => clearTimeout(timer);
  }, [query]);
  const brands = useQuery({
    queryKey: ["vinted-brands", convId, category, search],
    queryFn: () => api.nativeMarketplaces.vintedBrands(convId, search, category),
    enabled: !disabled,
    staleTime: 60_000,
    retry: false,
  });
  return <div>
    <SearchableSelect id={id} fieldKey={fieldKey} value={value} options={brands.data?.options ?? []}
      disabled={disabled} selectionOnly maxLength={100} choicesVerified={false} placeholder="Search Vinted brands"
      onSearch={setQuery} onChange={onChange} />
    {brands.isFetching && <p role="status" className="mt-1 text-xs text-muted-foreground">Searching brands…</p>}
    {brands.isError && <p role="alert" className="mt-1 text-xs text-warning-foreground">{brands.error.message}</p>}
  </div>;
}
