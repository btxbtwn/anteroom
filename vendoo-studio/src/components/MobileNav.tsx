import type { ComponentType } from "react";
import {
  BoxIcon,
  CalendarClockIcon,
  ChartColumnIcon,
  GaugeIcon,
  MenuIcon,
  MessageCircleIcon,
  Rows3Icon,
  SettingsIcon,
  StoreIcon,
} from "lucide-react";
import { cn } from "../lib/utils";
import { Sheet, SheetPopup, SheetTitle } from "./ui/sheet";
import type { WorkspaceView } from "./workspaceCrumbs";

export type MobileNavTarget = "assistant" | "listings" | "sourcing" | "analytics" | "usage" | "settings" | "marketplaces" | "automations";

/** Same order as the desktop sidebar footer: home and inventory, the day's work, insight, settings. */
const ITEMS: { id: MobileNavTarget; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { id: "assistant", label: "Assistant", icon: MessageCircleIcon },
  { id: "listings", label: "Inventory", icon: Rows3Icon },
  { id: "sourcing", label: "Sourcing", icon: BoxIcon },
  { id: "analytics", label: "Analytics", icon: ChartColumnIcon },
  { id: "marketplaces", label: "Marketplaces", icon: StoreIcon },
  { id: "automations", label: "Automations", icon: CalendarClockIcon },
  { id: "usage", label: "Usage", icon: GaugeIcon },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];

/** The phone's way between pages: a drawer from the left, as T3 Code's mobile sidebar. */
export function MobileNavButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      className={cn("sidebar-icon-btn mobile-nav-btn", className)}
      aria-label="Open navigation"
      title="Navigation"
      onClick={onClick}
    >
      <MenuIcon className="size-4.5" />
    </button>
  );
}

export function MobileNav({
  open,
  onOpenChange,
  activeView,
  onNavigate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeView: WorkspaceView;
  onNavigate: (target: MobileNavTarget) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPopup
        side="left"
        showCloseButton={false}
        className="max-w-80 pt-[env(safe-area-inset-top,0px)] pb-[env(safe-area-inset-bottom,0px)] pl-[env(safe-area-inset-left,0px)]"
      >
        <div className="flex h-14 shrink-0 items-center gap-1 px-5">
          <SheetTitle className="text-[22px] font-extrabold tracking-[-0.04em]">
            Anteroom
          </SheetTitle>
        </div>
        <nav aria-label="Pages" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-3">
          {ITEMS.map(({ id, label, icon: Icon }) => {
            const active = activeView === id;
            return (
              <button
                key={id}
                type="button"
                data-active={active}
                aria-current={active ? "page" : undefined}
                className="flex min-h-12 w-full cursor-pointer items-center gap-3 rounded-xl px-3 text-left text-base font-medium text-foreground/85 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring data-[active=true]:bg-accent data-[active=true]:text-foreground"
                onClick={() => {
                  onOpenChange(false);
                  onNavigate(id);
                }}
              >
                <Icon className="size-5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{label}</span>
              </button>
            );
          })}
        </nav>
      </SheetPopup>
    </Sheet>
  );
}
