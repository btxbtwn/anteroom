import { useEffect, useState } from "react";

/** The hosted web app; the Mac app's window shows it when the seller signs in. */
export const HOSTED_ORIGIN = "https://myanteroom.com";

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
  const [mac, setMac] = useState(() => typeof window !== "undefined" && isMacApp());
  useEffect(() => {
    const ready = () => setMac(true);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  return mac;
}

export type MacChatGPTSignIn = { ok: boolean; signed_in?: boolean; pending?: boolean; error?: string | null };

/** The Mac app's ChatGPT sign-in, reachable from the account window. */
export type MacChatGPTBridge = {
  chatgpt_sign_in: () => Promise<MacChatGPTSignIn>;
  chatgpt_sign_in_status: () => Promise<MacChatGPTSignIn>;
  chatgpt_sign_in_cancel: () => Promise<MacChatGPTSignIn>;
};

/** Null outside the Mac app, or in a Mac app that predates the sign-in bridge. */
export function macChatGPTBridge(target: Window = window): MacChatGPTBridge | null {
  const api = (target as unknown as { pywebview?: { api?: Partial<MacChatGPTBridge> } }).pywebview?.api;
  return api?.chatgpt_sign_in && api.chatgpt_sign_in_status && api.chatgpt_sign_in_cancel ? (api as MacChatGPTBridge) : null;
}
