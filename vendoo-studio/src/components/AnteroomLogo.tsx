import { useId } from "react";

// The app icon's drawing (desktop/make_icon.py) in its 824-unit tile:
// rooms receding toward a lit doorway.
const FLOOR = 768;
const ARCH_TOP = 488;
// The arches fade out over their lowest stretch instead of ending on a hard line.
const FADE_TOP = 560;
const ARCHES: ReadonlyArray<readonly [number, number, string]> = [
  [192, 832, "#431084"],
  [256, 768, "#9e3081"],
  [320, 704, "#e8605b"],
  [384, 640, "#feb251"],
  [448, 576, "#fefaf0"],
];

function archPath(left: number, right: number) {
  const radius = (right - left) / 2;
  return `M${left} ${FLOOR}V${ARCH_TOP}A${radius} ${radius} 0 0 1 ${right} ${ARCH_TOP}V${FLOOR}Z`;
}

export function AnteroomLogo({ className }: { className?: string }) {
  const id = useId();
  const fade = `${id}-fade`;
  const mask = `${id}-mask`;
  return (
    <svg className={className} viewBox="100 100 824 824" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={fade} x1="0" y1={FADE_TOP} x2="0" y2={FLOOR} gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fff" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0.7" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id={mask} maskUnits="userSpaceOnUse" x="100" y="100" width="824" height="824">
          <rect x="100" y="100" width="824" height="824" fill={`url(#${fade})`} />
        </mask>
      </defs>
      <rect x="100" y="100" width="824" height="824" rx="185.4" fill="#090909" />
      <g mask={`url(#${mask})`}>
        {ARCHES.map(([left, right, fill]) => (
          <path key={left} d={archPath(left, right)} fill={fill} />
        ))}
      </g>
    </svg>
  );
}
