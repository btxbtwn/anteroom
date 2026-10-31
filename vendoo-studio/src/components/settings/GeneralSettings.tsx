import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ListingNames } from "../../api/types";
import { frontendBuildState } from "../buildStamp";
import { useStudioUpdate } from "../UpdateButton";
import { ChangelogDialog } from "../ChangelogDialog";
import { PreviousVersionsDialog } from "../PreviousVersionsDialog";
import { applyTheme, normalizeTheme, type ThemePreference } from "../../theme";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsCode, SettingsNote, SettingsRow, SettingsSection, SettingsSelect } from "./SettingsLayout";
import { useWorkspaceRuntime } from "../../app/runtime";

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
        description="Dark matches the Anteroom workbench. Light inverts the same shell. System follows the OS."
        control={
          <SettingsSelect<ThemePreference>
            aria-label="Theme"
            value={theme}
            disabled={isPending || mutation.isPending}
            onChange={(value) => mutation.mutate(value)}
            options={[
              { value: "system", label: "System" },
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
        }
      />
      <SettingsRow
        id="listing-names"
        title="Listing names"
        description="What the sidebar calls each listing. By SKU, a listing without a SKU keeps its title."
        control={
          <SettingsSelect<ListingNames>
            aria-label="Listing names"
            value={data?.listing_names ?? "title"}
            disabled={isPending || namesMutation.isPending}
            onChange={(value) => namesMutation.mutate(value)}
            options={[
              { value: "title", label: "Title" },
              { value: "sku", label: "SKU" },
            ]}
          />
        }
      />
      {mutation.error || namesMutation.error ? (
        <SettingsNote tone="error">{mutation.error?.message || namesMutation.error?.message}</SettingsNote>
      ) : null}
    </SettingsSection>
  );
}

function DesktopAboutVersionRow({ version }: { version: string }) {
  const { available, busy, downloaded, description, iconTooltip, onClick, settingsLabel } = useStudioUpdate();
  const build = frontendBuildState(__STUDIO_BUILD__, version === "…" ? null : version);
  return (
    <SettingsRow
      title={
        <>
          Version
          <SettingsCode>{build.outdated ? build.label : version}</SettingsCode>
        </>
      }
      description={build.outdated ? build.title : description}
      control={
        <Button
          size="xs"
          variant={busy || available || downloaded ? "default" : "outline"}
          onClick={onClick}
          disabled={busy}
          aria-busy={busy || undefined}
          title={iconTooltip}
        >
          {busy ? <Spinner size="xs" /> : null}
          {settingsLabel}
        </Button>
      }
    />
  );
}

function AboutVersionRow({ version }: { version: string }) {
  const { hosted } = useWorkspaceRuntime();
  if (!hosted) return <DesktopAboutVersionRow version={version} />;
  const build = frontendBuildState(__STUDIO_BUILD__, version === "…" ? null : version);
  return (
    <SettingsRow
      title={<>Version<SettingsCode>{build.outdated ? build.label : version}</SettingsCode></>}
      description={build.outdated
        ? `This tab is running ${build.version}. The web app is on ${version}; reload to use that version.`
        : "Web updates are installed on the server. Reload this tab to load the current version."}
      control={<Button size="xs" variant="outline" onClick={() => window.location.reload()}>Reload app</Button>}
    />
  );
}

function WhatsNewRow() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingsRow
        title="What's new"
        description="Every change to Anteroom, newest first."
        control={
          <Button size="xs" variant="outline" onClick={() => setOpen(true)}>
            View changelog
          </Button>
        }
      />
      <ChangelogDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

function PreviousVersionsRow() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingsRow
        title="Previous versions"
        description="Go back to an earlier build if the current one isn't working."
        control={
          <Button size="xs" variant="outline" onClick={() => setOpen(true)}>
            Choose version
          </Button>
        }
      />
      <PreviousVersionsDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

export function GeneralSettings({ onOpenSetupGuide }: { onOpenSetupGuide?: () => void }) {
  const { hosted } = useWorkspaceRuntime();
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
  });
  return (
    <>
      <AppearanceSection />
      {!hosted ? <SettingsSection id="setup-guide" title="Setup guide">
        <SettingsRow
          title="First-run tutorial"
          description="Walk through ChatGPT or MiMo, Brave Search, Connect Chrome, and how the listing workspace is laid out."
          control={
            <Button size="xs" variant="outline" onClick={onOpenSetupGuide}>
              Open setup guide
            </Button>
          }
        />
      </SettingsSection> : null}
      <SettingsSection id="about" title="About">
        <AboutVersionRow version={status?.version || "…"} />
        <WhatsNewRow />
        {!hosted ? <PreviousVersionsRow /> : null}
      </SettingsSection>
    </>
  );
}
