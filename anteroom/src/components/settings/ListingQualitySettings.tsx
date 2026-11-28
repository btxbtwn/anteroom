import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ListingQualityConsent } from "../../api/client";
import { useWorkspaceRuntime } from "../../app/runtime";
import { Switch } from "../ui/switch";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function listingQualityConsentKey(accountId: string | undefined) {
  return ["settings-listing-quality-consent", accountId] as const;
}

/** Hosted only: the seller's opt-in to listing-quality counts. Off by default; off deletes what was recorded. */
export function ListingQualitySettings() {
  const { account } = useWorkspaceRuntime();
  const client = useQueryClient();
  const queryKey = listingQualityConsentKey(account?.id);
  const consent = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.settings.listingQualityConsent({ signal }),
  });
  const save = useMutation({
    mutationFn: api.settings.setListingQualityConsent,
    onMutate: async (enabled) => {
      await client.cancelQueries({ queryKey });
      const previous = client.getQueryData<ListingQualityConsent>(queryKey);
      client.setQueryData<ListingQualityConsent>(queryKey, { enabled });
      return { previous };
    },
    onError: (_error, _enabled, context) => client.setQueryData(queryKey, context?.previous),
    onSuccess: (result) => client.setQueryData(queryKey, result),
  });
  const error = consent.error || save.error;

  return (
    <SettingsSection id="listing-quality" title="Listing quality">
      <SettingsRow
        title="Help improve listings"
        description="Share counts of the fields you change, clear or fill in when reviewing a generated draft. We also count when generation overwrites a value you set. Your photos, listing text, chats and field values stay out of these reports."
        inlineControl
        control={
          <Switch
            aria-label="Help improve listings"
            checked={consent.data?.enabled === true}
            disabled={!consent.data || save.isPending}
            onCheckedChange={(enabled) => save.mutate(enabled)}
          />
        }
      >
        {error ? <SettingsNote tone="error">{`Couldn’t ${save.error ? "save" : "load"} this setting. ${error.message}`}</SettingsNote> : null}
        <SettingsNote>
          Off unless you turn it on. Turning it off deletes the counts recorded so far. <a className="underline underline-offset-2" href="/privacy#listing-quality" target="_blank" rel="noreferrer">Privacy policy</a>
        </SettingsNote>
      </SettingsRow>
    </SettingsSection>
  );
}
