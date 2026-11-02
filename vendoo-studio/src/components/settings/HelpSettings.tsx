import { ChevronRightIcon } from "lucide-react";
import { ChatMarkdown } from "../ChatMarkdown";
import { HELP_TOPICS, helpTopicMarkdown } from "./helpTopics";
import { SettingsSection } from "./SettingsLayout";

export function HelpSettings() {
  return (
    <SettingsSection id="help" title="How to">
      {HELP_TOPICS.map((topic) => (
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
  );
}
