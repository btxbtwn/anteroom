import sprite from "../assets/ant-avatar.png";
import { useChatBusy } from "./chat/liveChatStore";

export function AntAvatar({ convId, working }: { convId: string; working?: boolean }) {
  const busy = useChatBusy(convId);
  return (
    <img className="ant-avatar shrink-0" src={sprite} width={24} height={24} alt=""
      data-working={(working ?? busy) || undefined} />
  );
}
