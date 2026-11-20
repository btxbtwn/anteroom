import { cn } from "../lib/utils";

const TONES = {
  success: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
  info: "bg-info",
  muted: "bg-muted-foreground/60",
} as const;

/** The small connection dot T3 Code puts beside status text. */
export function StatusDot({ tone, className }: { tone: keyof typeof TONES; className?: string }) {
  return <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", TONES[tone], className)} />;
}
