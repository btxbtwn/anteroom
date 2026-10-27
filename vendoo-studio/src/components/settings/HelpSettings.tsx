import { ChevronRightIcon } from "lucide-react";
import { ChatMarkdown } from "../ChatMarkdown";
import { useVendooEnabled } from "../../vendooMode";
import { helpTopicMarkdown, helpTopicsForMode } from "./helpTopics";
import { SettingsSection } from "./SettingsLayout";

export function HelpSettings() {
  const topics = helpTopicsForMode(useVendooEnabled());
  return (
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
  );
}
