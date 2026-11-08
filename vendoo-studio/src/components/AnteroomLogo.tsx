// The app icon's drawing (desktop/make_icon.py) in its 824-unit tile:
// rooms receding toward a lit doorway.
const FLOOR = 768;
const ARCH_TOP = 488;
const ARCHES: ReadonlyArray<readonly [number, number, string]> = [
  [192, 832, "#152969"],
  [256, 768, "#244ab4"],
  [320, 704, "#346bf1"],
  [384, 640, "#90b3fe"],
  [448, 576, "#ffffff"],
];

function archPath(left: number, right: number) {
  const radius = (right - left) / 2;
  return `M${left} ${FLOOR}V${ARCH_TOP}A${radius} ${radius} 0 0 1 ${right} ${ARCH_TOP}V${FLOOR}Z`;
}

export function AnteroomLogo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="100 100 824 824" aria-hidden="true" focusable="false">
      <rect x="100" y="100" width="824" height="824" rx="185.4" fill="#090909" />
      {ARCHES.map(([left, right, fill]) => (
        <path key={left} d={archPath(left, right)} fill={fill} />
      ))}
    </svg>
  );
}
