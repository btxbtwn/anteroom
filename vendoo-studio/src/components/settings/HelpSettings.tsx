import { ChatMarkdown } from "../ChatMarkdown";
import { HELP_TOPICS, helpTopicMarkdown } from "./helpTopics";
import { SettingsSection } from "./SettingsLayout";

export function HelpSettings() {
  return (
    <SettingsSection id="help" title="How to">
      {HELP_TOPICS.map((topic) => (
        <details key={topic.id} id={topic.id} className="settings-row settings-help-topic">
          <summary className="settings-row-title">{topic.title}</summary>
          <ChatMarkdown text={helpTopicMarkdown(topic)} />
        </details>
      ))}
    </SettingsSection>
  );
}
