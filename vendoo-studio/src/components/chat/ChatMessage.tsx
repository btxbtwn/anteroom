import type { Message } from "../../api/types";
import { ChatMarkdown } from "../ChatMarkdown";
import { EvidenceCard } from "../EvidenceCard";
import { SoldCompsCard } from "../SoldCompsCard";
import { assistantDisplayText, isCompResearch, isPhotoAnalysis } from "./assistantDisplay";

export function ChatMessage({ message: m }: { message: Message }) {
  if (m.role === "user") {
    return (
      <div className="msg msg-user">
        <ChatMarkdown text={m.text} lineBreaks />
      </div>
    );
  }

  if (m.role === "system" && isPhotoAnalysis(m.text)) {
    return <EvidenceCard text={m.text} messageId={m.id} />;
  }

  if (m.role === "system" && isCompResearch(m.text)) {
    return <SoldCompsCard text={m.text} messageId={m.id} />;
  }

  if (m.role === "system") {
    return (
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "4px 0" }}>
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-border-bright)", flexShrink: 0, marginTop: 6 }} />
        <span className="msg-system" style={{ padding: 0, borderLeft: "none", maxWidth: "none", whiteSpace: "pre-wrap" }}>{m.text}</span>
      </div>
    );
  }

  const visible = assistantDisplayText(m.text);
  if (!visible) return null;
  return (
    <div className="msg msg-assistant" data-citation-source={m.id}>
      <ChatMarkdown text={visible} />
    </div>
  );
}
