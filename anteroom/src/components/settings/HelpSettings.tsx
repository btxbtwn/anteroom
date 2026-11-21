import { ChevronRightIcon, ExternalLinkIcon } from "lucide-react";
import { useWorkspaceRuntime } from "../../app/runtime";
import { HOSTED_ORIGIN } from "../../lib/macApp";
import { ChatMarkdown } from "../ChatMarkdown";
import { Button } from "../ui/button";
import { helpTopicMarkdown, helpTopicsFor } from "./helpTopics";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

export function HelpSettings() {
  const { hosted } = useWorkspaceRuntime();
  const topics = helpTopicsFor(hosted);
  const privacyHref = hosted ? "/privacy" : `${HOSTED_ORIGIN}/privacy`;
  const termsHref = hosted ? "/terms" : `${HOSTED_ORIGIN}/terms`;

  return (
    <>
      <SettingsSection id="help" title="How to">
        {topics.map((topic) => (
          <details
            key={topic.id}
            id={topic.id}
            tabIndex={-1}
            data-slot="settings-row"
            className="group px-3 outline-none sm:px-4"
          >
            <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 py-3 text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
              {topic.title}
            </summary>
            <div className="pb-4 pl-5.5 text-sm text-muted-foreground">
              <ChatMarkdown text={helpTopicMarkdown(topic)} />
            </div>
          </details>
        ))}
      </SettingsSection>
      <SettingsSection id="support" title="Support and legal">
        <SettingsRow
          title="Contact support"
          description="Questions, bug reports, or help with your account."
          control={
            <Button size="xs" variant="outline" render={<a href="mailto:support@myanteroom.com" />}>
              support@myanteroom.com
            </Button>
          }
        />
        <SettingsRow
          title="Privacy Policy"
          description="What data Anteroom stores, which service providers we use, and how to delete your account."
          control={
            <Button size="xs" variant="outline" render={<a href={privacyHref} target="_blank" rel="noreferrer" />}>
              <ExternalLinkIcon />
              Open
            </Button>
          }
        />
        <SettingsRow
          title="Terms of Service"
          description="Terms covering your account, third-party marketplaces, and AI-assisted drafts."
          control={
            <Button size="xs" variant="outline" render={<a href={termsHref} target="_blank" rel="noreferrer" />}>
              <ExternalLinkIcon />
              Open
            </Button>
          }
        />
      </SettingsSection>
    </>
  );
}
