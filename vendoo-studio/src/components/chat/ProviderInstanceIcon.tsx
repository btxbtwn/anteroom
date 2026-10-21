/**
 * A provider's mark wherever Studio shows one: the composer picker, Settings →
 * Providers and Settings → Usage.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.):
 * apps/web/src/components/chat/ProviderInstanceIcon.tsx. Studio has one
 * instance per provider, so the instance badge, accent colour and ACP registry
 * icons T3 draws for extra instances are left out.
 */
import { memo } from "react";

import type { ListingProviderId } from "../../api/types";
import { cn } from "../../lib/utils";
import { ClaudeAI, CursorIcon, MiMoIcon, OpenAI, type Icon } from "../providerIcons";

const PROVIDER_ICON_BY_PROVIDER: Record<ListingProviderId, Icon> = {
  chatgpt: OpenAI,
  claude: ClaudeAI,
  cursor: CursorIcon,
  mimo: MiMoIcon,
};

/** Settings ids, plus the names the provider status reports (`xiaomi-mimo`). */
export function resolveProviderId(provider: string | null | undefined): ListingProviderId | null {
  if (provider === "chatgpt" || provider === "claude" || provider === "cursor") return provider;
  if (provider === "mimo" || provider === "xiaomi-mimo") return "mimo";
  return null;
}

function providerInitials(displayName: string): string {
  return (displayName.trim().charAt(0) || "?").toUpperCase();
}

export const ProviderInstanceIcon = memo(function ProviderInstanceIcon(props: {
  driverKind: ListingProviderId | string;
  displayName: string;
  className?: string;
  iconClassName?: string;
}) {
  const provider = resolveProviderId(props.driverKind);
  const Icon = provider ? PROVIDER_ICON_BY_PROVIDER[provider] : null;
  return (
    <span
      className={cn(
        "relative isolate z-30 inline-flex shrink-0 items-center justify-center overflow-visible",
        props.className,
      )}
      title={props.displayName}
    >
      {Icon ? (
        <Icon className={cn("size-5 shrink-0", props.iconClassName)} aria-hidden />
      ) : (
        <span className={cn("text-3xs font-semibold leading-none", props.iconClassName)}>
          {providerInitials(props.displayName)}
        </span>
      )}
    </span>
  );
});
