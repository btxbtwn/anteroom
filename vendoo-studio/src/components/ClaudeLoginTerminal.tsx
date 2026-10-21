import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { XIcon } from "lucide-react";
import { api } from "../api/client";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";

type TerminalState = "running" | "succeeded" | "failed";

/**
 * Resolve a theme token to the rgb() string xterm can parse. T3's tokens are
 * oklch and color-mix, so let the browser compute them through a canvas.
 */
function themeColor(host: HTMLElement, token: string, fallback: string): string {
  const probe = document.createElement("span");
  probe.style.color = `var(${token}, ${fallback})`;
  host.appendChild(probe);
  const computed = getComputedStyle(probe).color;
  probe.remove();
  const canvas = document.createElement("canvas").getContext("2d");
  if (!canvas) return fallback;
  canvas.fillStyle = computed;
  canvas.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
  return a === 255 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
}

/** Open links the way Studio's other external links open: in the seller's browser. */
function openLink(_event: MouseEvent, url: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noreferrer";
  anchor.click();
}

/**
 * A small terminal running `claude auth login`: the seller sees Claude Code's own
 * prompts and can paste its code. It runs that one command, not a shell. A
 * successful sign-in closes it; a failed one stays open so the error can be read.
 */
export function ClaudeLoginTerminal({ onClose }: { onClose: (signedIn: boolean) => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const [state, setState] = useState<TerminalState>("running");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const styles = getComputedStyle(document.documentElement);
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 12,
      fontFamily: styles.getPropertyValue("--font-mono").trim() || "ui-monospace, Menlo, monospace",
      theme: {
        background: themeColor(host, "--terminal-background", "#0a0a0a"),
        foreground: themeColor(host, "--terminal-foreground", "#f5f5f5"),
        cursor: themeColor(host, "--terminal-cursor", "#b4cbff"),
        selectionBackground: themeColor(host, "--terminal-selection-background", "rgb(180 203 255 / 25%)"),
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.loadAddon(new WebLinksAddon(openLink));
    terminal.open(host);
    fit.fit();

    const socket = new WebSocket(api.settings.claudeLoginTerminalUrl(terminal.cols, terminal.rows));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        terminal.write(new Uint8Array(event.data));
        return;
      }
      const frame = JSON.parse(event.data as string) as { type: string; code?: number; message?: string };
      if (frame.type === "exit" && frame.code === 0) {
        setState("succeeded");
        onCloseRef.current(true);
      } else if (frame.type === "exit") {
        setState("failed");
      } else if (frame.type === "error") {
        setState("failed");
        setMessage(frame.message ?? "Could not start Claude sign-in.");
      }
    };
    socket.onclose = () => setState((current) => (current === "running" ? "failed" : current));
    const input = terminal.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
    const resize = terminal.onResize(({ cols, rows }) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", cols, rows }));
    });
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(host);
    terminal.focus();

    return () => {
      observer.disconnect();
      input.dispose();
      resize.dispose();
      socket.close();
      terminal.dispose();
    };
  }, []);

  return (
    <div className="grid gap-2">
      <div className="flex min-w-0 items-center gap-2 text-xs">
        {state === "running" ? <Spinner size="sm" tone="muted" /> : null}
        <span
          role="status"
          className={
            state === "failed"
              ? "min-w-0 flex-1 text-destructive-foreground"
              : state === "succeeded"
                ? "min-w-0 flex-1 text-success-foreground"
                : "min-w-0 flex-1 text-muted-foreground"
          }
        >
          {message ??
            (state === "running"
              ? "Finish signing in in the browser. If Claude asks for a code, paste it here."
              : state === "succeeded"
                ? "Signed in to Claude."
                : "Sign-in did not finish. Close it and sign in again.")}
        </span>
        <Button size="xs" variant="ghost-muted" onClick={() => onClose(state === "succeeded")}>
          <XIcon />
          Close
        </Button>
      </div>
      <div className="relative h-64 overflow-hidden rounded-md border border-border bg-(--terminal-background) p-2">
        <div ref={hostRef} className="size-full" />
      </div>
    </div>
  );
}
