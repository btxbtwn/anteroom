/** How the assistant's past chats are dated and grouped, newest first, as ChatGPT and Claude list theirs. */

export type ChatGroup = "Today" | "Yesterday" | "Previous 7 days" | "Older";

interface Dated {
  updated_at: string | null;
  created_at: string | null;
}

/** The server writes naive UTC timestamps. */
function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

function lastActive(chat: Dated): Date | null {
  return parse(chat.updated_at) ?? parse(chat.created_at);
}

/** Whole local calendar days from `date` to `now`. */
function daysAgo(date: Date, now: Date): number {
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((start(now) - start(date)) / 86_400_000);
}

export function chatGroup(chat: Dated, now = new Date()): ChatGroup {
  const date = lastActive(chat);
  if (!date) return "Older";
  const days = daysAgo(date, now);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  return "Older";
}

/** "3:42 PM" today, "Mon" this week, "Oct 3" before, "Oct 3, 2025" in another year. */
export function chatTime(chat: Dated, now = new Date()): string {
  const date = lastActive(chat);
  if (!date) return "";
  const days = daysAgo(date, now);
  if (days <= 0) return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  if (days < 7) return date.toLocaleDateString(undefined, { weekday: "short" });
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** Chats newest first, under their group headings; groups with no chats are left out. */
export function groupChats<T extends Dated>(chats: readonly T[], now = new Date()): { group: ChatGroup; chats: T[] }[] {
  const sorted = [...chats].sort((a, b) => (lastActive(b)?.getTime() ?? 0) - (lastActive(a)?.getTime() ?? 0));
  const groups: { group: ChatGroup; chats: T[] }[] = [];
  for (const chat of sorted) {
    const group = chatGroup(chat, now);
    const last = groups[groups.length - 1];
    if (last?.group === group) last.chats.push(chat);
    else groups.push({ group, chats: [chat] });
  }
  return groups;
}
