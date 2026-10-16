import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ListingNames } from "../../api/types";
import { useStudioUpdate } from "../UpdateButton";
import { ChangelogDialog } from "../ChangelogDialog";
import { applyTheme, normalizeTheme, type ThemePreference } from "../../theme";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

function AppearanceSection() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const mutation = useMutation({
    mutationFn: (theme: ThemePreference) => api.settings.setUi({ theme }),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-ui"], payload);
      applyTheme(normalizeTheme(payload.theme));
    },
  });
  const namesMutation = useMutation({
    mutationFn: (listingNames: ListingNames) => api.settings.setUi({ listing_names: listingNames }),
    onSuccess: (payload) => queryClient.setQueryData(["settings-ui"], payload),
  });
  const theme = normalizeTheme(data?.theme);

  return (
    <SettingsSection id="appearance" title="Appearance">
      <SettingsRow
        title="Theme"
        description="Dark matches the Studio workbench. Light inverts the same shell. System follows the OS."
        control={
          <select
            className="input settings-model-select"
            aria-label="Theme"
            value={theme}
            disabled={isPending || mutation.isPending}
            onChange={(event) => mutation.mutate(event.target.value as ThemePreference)}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="system">System</option>
          </select>
        }
      />
      <SettingsRow
        id="listing-names"
        title="Listing names"
        description="What the sidebar calls each listing. By SKU, a listing without a SKU keeps its title."
        control={
          <select
            className="input settings-model-select"
            aria-label="Listing names"
            value={data?.listing_names ?? "title"}
            disabled={isPending || namesMutation.isPending}
            onChange={(event) => namesMutation.mutate(event.target.value as ListingNames)}
          >
            <option value="title">Title</option>
            <option value="sku">SKU</option>
          </select>
        }
      />
    </SettingsSection>
  );
}

function AboutVersionRow({ version }: { version: string }) {
  const { available, busy, downloaded, description, iconTooltip, onClick, settingsLabel } = useStudioUpdate();
  return (
    <SettingsRow
      title={
        <span className="settings-version-title">
          Version
          <code className="settings-row-code">{version}</code>
        </span>
      }
      description={description}
      control={
        <button
          type="button"
          className={`btn btn-sm ${busy || available || downloaded ? "btn-primary" : "btn-outline"}${busy ? " is-busy" : ""}`}
          onClick={onClick}
          disabled={busy}
          aria-busy={busy || undefined}
          title={iconTooltip}
        >
          {settingsLabel}
        </button>
      }
    />
  );
}

function WhatsNewRow() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingsRow
        title="What's new"
        description="Every change to List This Studio, newest first."
        control={
          <button type="button" className="btn btn-sm btn-outline" onClick={() => setOpen(true)}>
            View changelog
          </button>
        }
      />
      <ChangelogDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

export function GeneralSettings({ onOpenSetupGuide }: { onOpenSetupGuide?: () => void }) {
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
  });
  return (
    <>
      <AppearanceSection />
      <SettingsSection id="setup-guide" title="Setup guide">
        <SettingsRow
          title="First-run tutorial"
          description="Walk through ChatGPT or MiMo, Brave Search, Connect Chrome, and how the listing workspace is laid out."
          control={
            <button type="button" className="btn btn-sm btn-outline" onClick={onOpenSetupGuide}>
              Open setup guide
            </button>
          }
        />
      </SettingsSection>
      <SettingsSection id="about" title="About">
        <AboutVersionRow version={status?.version || "…"} />
        <WhatsNewRow />
      </SettingsSection>
    </>
  );
}
