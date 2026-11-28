import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { settingsUiQuery } from "../../api/pageQueries";
import { Switch } from "../ui/switch";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function ExperimentalSettings() {
  const client = useQueryClient();
  const prefs = useQuery(settingsUiQuery);
  const save = useMutation({
    mutationFn: api.settings.setUi,
    onSuccess: (result, change) => {
      client.setQueryData(["settings-ui"], result);
      if (!change.listing_mode) return;
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
            onCheckedChange={value => save.mutate({ listing_mode: value ? "vendoo" : "direct" })}
          />
        }
      >
        {prefs.error || (save.error && save.variables?.listing_mode) ? <SettingsNote tone="error">{prefs.error?.message || save.error?.message}</SettingsNote> : null}
      </SettingsRow>
      <SettingsRow
        id="jev-field-review"
        title="Check draft fields with Jev"
        description="After generation, Jev checks selected garment details against the photo notes and corrects clear mistakes. Uses managed AI or your connected OpenRouter account."
        control={
          <Switch
            aria-label="Check draft fields with Jev"
            checked={prefs.data?.jev_field_review ?? false}
            disabled={prefs.isPending || prefs.isError || save.isPending}
            onCheckedChange={value => save.mutate({ jev_field_review: value })}
          />
        }
      >
        {save.error && save.variables?.jev_field_review !== undefined ? <SettingsNote tone="error">{save.error.message}</SettingsNote> : null}
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
