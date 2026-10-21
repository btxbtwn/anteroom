/** Listing AI provider logos for Settings and the status bar. */

import {
  PROVIDER_LOGOS,
  hasProviderLogo,
  resolveProviderLogoId,
  type ProviderLogoId,
} from "./providerLogos";

export { hasProviderLogo, resolveProviderLogoId };
export type { ProviderLogoId };

export function providerLogoColor(id: string): string {
  if (!hasProviderLogo(id)) return "#5C6578";
  return PROVIDER_LOGOS[id].color;
}

export function ProviderLogo({
  id,
  label,
  size = 16,
}: {
  id: string;
  label: string;
  size?: number;
}) {
  const logo = hasProviderLogo(id) ? PROVIDER_LOGOS[id] : null;

  if (logo) {
    return (
      <span
        className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-sm bg-white shadow-xs/5 ring-1 ring-black/5"
        title={label}
        aria-label={label}
        style={{ width: size, height: size }}
      >
        <svg
          width={size - 2}
          height={size - 2}
          viewBox={logo.viewBox}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden="true"
          className="block"
        >
          {logo.paths.map((path, index) => (
            <path
              key={index}
              d={path.d}
              fill={path.fill || logo.color}
              fillRule={path.fillRule}
              clipRule={path.clipRule}
            />
          ))}
        </svg>
      </span>
    );
  }

  const letter = (label || id || "?").trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-sm bg-muted-foreground"
      title={label}
      aria-label={label}
      style={{ width: size, height: size }}
    >
      <span className="text-[9px] leading-none font-bold tracking-tight text-background" aria-hidden="true">
        {letter}
      </span>
    </span>
  );
}
