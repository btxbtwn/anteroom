import { useWorkspaceRuntime } from "../../app/runtime";
import { useEffect, useRef, useState, type ComponentType, type FocusEvent, type PointerEvent } from "react";
import {
  CalendarClockIcon,
  ChartColumnIcon,
  HomeIcon,
  MessageCircleIcon,
  PinIcon,
  PinOffIcon,
  SettingsIcon,
  StoreIcon,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { UpdateButton } from "../UpdateButton";
import type { WorkspaceView } from "../workspaceCrumbs";
import { usePanelCollapsed } from "../PanelResizeHandle";
import { pageLoaders } from "../../app/pageLoaders";
import { useNavigationIntent } from "../../lib/useNavigationIntent";
import { isConfirmDialogOpen } from "../../ui/confirmDialog";

/**
 * A mouse must rest on the rail this long before it widens, so a pointer
 * crossing it on the way to the listings does not open it (GitLab's sidebar
 * peek uses 100–150 ms; Baymard measures 300–500 ms for hover menus).
 */
export const NAV_RAIL_OPEN_DELAY_MS = 200;
/** It stays wide this long after the pointer leaves, so a wobble does not close it. */
export const NAV_RAIL_CLOSE_DELAY_MS = 400;

/** Pinned open, or narrowed to icons, on this device. */
function useNavRailPinned() {
  const [collapsed, setCollapsed] = usePanelCollapsed("nav-rail", true);
  return [!collapsed, (pinned: boolean) => setCollapsed(!pinned)] as const;
}

function isTyping(target: EventTarget | null) {
  return target instanceof HTMLElement
    && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
}

function focusVisible(element: Element) {
  try {
    return element.matches(":focus-visible");
  } catch {
    return true;
  }
}

/**
 * One page link: the icon stays where it is and the name is revealed as the
 * rail widens, so nothing moves under the pointer.
 */
function RailButton({
  label,
  page,
  icon: Icon,
  active,
  onClick,
}: {
  label: string;
  page?: keyof typeof pageLoaders;
  icon: ComponentType<{ className?: string }>;
  active?: boolean;
  onClick: () => void;
}) {
  const runtime = useWorkspaceRuntime();
  const disabled = Boolean(page && !runtime.views.has(page));
  const intent = useNavigationIntent(() => {
    if (page) void pageLoaders[page]().catch(() => {});
  });
  return (
    <button
      type="button"
      className={RAIL_ROW}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      data-active={active}
      onClick={onClick}
      disabled={disabled}
      {...intent}
    >
      <Icon />
      <span className={RAIL_LABEL}>{label}</span>
    </button>
  );
}

const RAIL_ROW =
  "flex h-8 w-full shrink-0 cursor-pointer items-center gap-2.5 overflow-hidden rounded-[var(--control-radius)] px-2 text-left text-sm font-medium whitespace-nowrap text-sidebar-muted-foreground outline-hidden ring-ring transition-colors hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-inset active:bg-sidebar-row-active disabled:pointer-events-none disabled:opacity-40 data-[active=true]:bg-sidebar-row-selected data-[active=true]:text-sidebar-foreground [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-[var(--sidebar-icon-color)] hover:[&>svg]:text-sidebar-foreground data-[active=true]:[&>svg]:text-sidebar-foreground";

/** Hidden from sight (not from screen readers) while the rail shows icons only. */
const RAIL_LABEL =
  "min-w-0 truncate opacity-0 transition-opacity duration-150 group-data-[expanded=true]/rail:opacity-100 motion-reduce:transition-none";

/**
 * The page links down the left edge of the desktop window, the way Slack and
 * VS Code keep theirs: icons at rest, names when a mouse rests on it or the
 * keyboard reaches it, laid over the page so nothing shifts. Pinned (the pin
 * button or `[`), it stays wide beside the listings. Phones use MobileNav.
 */
export function NavRail({
  activeView,
  onOpenHome,
  onOpenAnalytics,
  onOpenMarketplaces,
  onOpenAutomations,
  onOpenAssistant,
  onOpenSettings,
}: {
  activeView: WorkspaceView;
  onOpenHome: () => void;
  onOpenAnalytics: () => void;
  onOpenMarketplaces: () => void;
  onOpenAutomations: () => void;
  onOpenAssistant: () => void;
  onOpenSettings: () => void;
}) {
  const runtime = useWorkspaceRuntime();
  const [pinned, setPinned] = useNavRailPinned();
  const [peeking, setPeeking] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expanded = pinned || peeking;

  const clearTimer = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  const peekAfter = (open: boolean, delay: number) => {
    clearTimer();
    timer.current = setTimeout(() => {
      timer.current = null;
      setPeeking(open);
    }, delay);
  };
  useEffect(() => clearTimer, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "[" || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      if (isTyping(event.target) || isConfirmDialogOpen()) return;
      event.preventDefault();
      clearTimer();
      setPeeking(false);
      setPinned(!pinned);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pinned, setPinned]);

  const onPointerEnter = (event: PointerEvent) => {
    if (event.pointerType === "mouse") peekAfter(true, NAV_RAIL_OPEN_DELAY_MS);
  };
  const onPointerLeave = (event: PointerEvent) => {
    if (event.pointerType === "mouse") peekAfter(false, NAV_RAIL_CLOSE_DELAY_MS);
  };
  const onFocus = (event: FocusEvent) => {
    if (!focusVisible(event.target)) return;
    clearTimer();
    setPeeking(true);
  };
  const onBlur = (event: FocusEvent<HTMLElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    clearTimer();
    setPeeking(false);
  };

  return (
    <div className="nav-rail group/rail" data-app-sidebar data-expanded={expanded} data-pinned={pinned}>
      <div
        className="nav-rail-panel"
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
        onFocus={onFocus}
        onBlur={onBlur}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || !peeking) return;
          event.stopPropagation();
          clearTimer();
          setPeeking(false);
        }}
      >
        <nav aria-label="Pages" className="flex min-h-0 flex-1 flex-col gap-0.5">
          <RailButton page="home" label="Today" icon={HomeIcon} active={activeView === "home"} onClick={onOpenHome} />
          <RailButton page="assistant" label="Assistant" icon={MessageCircleIcon} active={activeView === "assistant"} onClick={onOpenAssistant} />
          <RailButton page="analytics" label="Analytics" icon={ChartColumnIcon} active={activeView === "analytics"} onClick={onOpenAnalytics} />
          <RailButton page="marketplaces" label="Marketplaces" icon={StoreIcon} active={activeView === "marketplaces"} onClick={onOpenMarketplaces} />
          <RailButton page="automations" label="Automations" icon={CalendarClockIcon} active={activeView === "automations"} onClick={onOpenAutomations} />
          <div className="flex-1" />
          <RailButton page="settings" label="Settings" icon={SettingsIcon} active={activeView === "settings"} onClick={onOpenSettings} />
        </nav>
        {runtime.hosted ? null : <div className="nav-rail-update"><UpdateButton /></div>}
        <button
          type="button"
          className={cn(RAIL_ROW, "mt-0.5")}
          aria-label={pinned ? "Let navigation narrow" : "Keep navigation open"}
          aria-pressed={pinned}
          aria-keyshortcuts="["
          onClick={() => {
            clearTimer();
            setPeeking(false);
            setPinned(!pinned);
          }}
        >
          {pinned ? <PinOffIcon /> : <PinIcon />}
          <span className={RAIL_LABEL}>{pinned ? "Unpin" : "Keep open"}</span>
          <kbd className={cn(RAIL_LABEL, "ml-auto rounded border border-sidebar-border px-1 font-sans text-xs text-sidebar-muted-foreground")}>[</kbd>
        </button>
      </div>
    </div>
  );
}
