import { InfoIcon } from "lucide-react";
import type { Message } from "../../api/types";
import { EvidenceCard } from "../EvidenceCard";
import { SoldCompsCard } from "../SoldCompsCard";
import { useVendooEnabled } from "../../vendooMode";
import { assistantDisplayText, isCompResearch, isPhotoAnalysis } from "./assistantDisplay";
import { AssistantMessageRow, UserMessageRow } from "./TimelineRows";
import { WorkLogRow } from "./WorkLog";

export function ChatMessage({ message: m }: { message: Message }) {
  const vendooOn = useVendooEnabled();
  if (m.role === "user") {
    return <UserMessageRow text={m.text} createdAt={m.created_at} attachments={m.attachments} />;
  }

  if (m.role === "system" && isPhotoAnalysis(m.text)) {
    return <EvidenceCard text={m.text} messageId={m.id} />;
  }

  if (m.role === "system" && isCompResearch(m.text)) {
    return <SoldCompsCard text={m.text} messageId={m.id} />;
  }

  if (m.role === "system") {
    return (
      <WorkLogRow
        wrapLabel
        icon={<InfoIcon className="size-4 text-icon-muted" />}
        label={<span className="whitespace-pre-wrap text-sm leading-relaxed">{m.text}</span>}
      />
    );
  }

  const visible = assistantDisplayText(m.text, vendooOn);
  if (!visible) return null;
  return <AssistantMessageRow text={visible} createdAt={m.created_at} messageId={m.id} />;
}
