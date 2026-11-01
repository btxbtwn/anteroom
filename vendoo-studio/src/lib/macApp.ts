import { useEffect, useState } from "react";

/** The hosted web app; the Mac app's window shows it when the seller signs in. */
export const HOSTED_ORIGIN = "https://peaceful-meadowstone-6944.fly.dev";

/** True inside the Mac app's window, which injects `window.pywebview`. */
export function isMacApp(target: Window = window) {
  return "pywebview" in target;
}

/** Tag the page so it leaves room for the window's traffic lights. */
export function markMacApp(target: Window = window) {
  const apply = () => target.document.documentElement.classList.add("desktop-app");
  if (isMacApp(target)) apply();
  target.addEventListener("pywebviewready", apply);
}

export function useMacApp() {
  const [mac, setMac] = useState(() => isMacApp());
  useEffect(() => {
    const ready = () => setMac(true);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  return mac;
}
