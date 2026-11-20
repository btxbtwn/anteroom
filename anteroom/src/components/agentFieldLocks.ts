/** Native labels and normalized keys name the same control. Group locks cover its children. */
export function agentFieldLocked(path: string, locks: readonly string[]): boolean {
  const normalize = (value: string) => value.toLowerCase().split(".").map(part => part.replace(/[^a-z0-9]/g, "")).join(".");
  const field = normalize(path);
  return locks.some(path => {
    const locked = normalize(path);
    return locked !== "" && (field === locked || field.startsWith(`${locked}.`) || locked.startsWith(`${field}.`));
  });
}
