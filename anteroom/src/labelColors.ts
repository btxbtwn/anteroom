export const LABEL_COLORS = ["gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"] as const;
export type LabelColor = (typeof LABEL_COLORS)[number];

const DOT: Record<LabelColor, string> = {
  gray: "bg-zinc-400", red: "bg-red-500", orange: "bg-orange-500", yellow: "bg-yellow-400", green: "bg-green-500",
  teal: "bg-teal-500", blue: "bg-blue-500", purple: "bg-purple-500", pink: "bg-pink-500",
};

/** The color the seller gave a label, matched without regard to case. */
export function labelColor(colors: Record<string, LabelColor> | undefined, label: string): LabelColor | null {
  if (!colors) return null;
  const key = label.trim().toLowerCase();
  const hit = Object.entries(colors).find(([name]) => name.toLowerCase() === key);
  return hit ? hit[1] : null;
}

export function labelDotClass(color: LabelColor): string {
  return DOT[color];
}
