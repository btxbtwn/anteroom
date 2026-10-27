import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";

/** Send to Vendoo is on unless Settings has turned it off. */
export function useVendooEnabled(): boolean {
  const { data } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
    staleTime: 15_000,
  });
  return data?.vendoo_enabled !== false;
}
