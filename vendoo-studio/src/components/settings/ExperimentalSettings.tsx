import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Switch } from "../ui/switch";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function ExperimentalSettings() {
  const client = useQueryClient();
  const prefs = useQuery({ queryKey: ["settings-ui"], queryFn: api.settings.ui });
  const save = useMutation({
    mutationFn: (enabled: boolean) => api.settings.setUi({ listing_mode: enabled ? "vendoo" : "direct" }),
    onSuccess: result => {
      client.setQueryData(["settings-ui"], result);
      void client.invalidateQueries({ queryKey: ["listing-fields"] });
      void client.invalidateQueries({ queryKey: ["native-editor-form"] });
    },
  });

  return (
    <SettingsSection id="experimental" title="Experimental">
      <SettingsRow
        id="listing-mode"
        title="Use Vendoo"
        description="On uses Vendoo for draft review and listing. Off uses direct marketplace forms and actions."
        control={
          <Switch
            aria-label="Use Vendoo"
            checked={prefs.data?.listing_mode === "vendoo"}
            disabled={prefs.isPending || prefs.isError || save.isPending}
            onCheckedChange={value => save.mutate(value)}
          />
        }
      >
        {prefs.error || save.error ? <SettingsNote tone="error">{prefs.error?.message || save.error?.message}</SettingsNote> : null}
      </SettingsRow>
      <SettingsRow
        id="hosted-marketplace-browser"
        title="Hosted marketplace browser"
        description="Create marketplace drafts from any device without installing an extension."
        status={<span id="hosted-marketplace-browser-status">Coming soon</span>}
        control={
          <Switch
            aria-label="Hosted marketplace browser"
            aria-describedby="hosted-marketplace-browser-status"
            checked={false}
            disabled
          />
        }
      />
    </SettingsSection>
  );
}
