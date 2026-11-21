import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon, ExternalLinkIcon } from "lucide-react";
import { CHROME_WEB_STORE_URL } from "../api/chromeBridge";
import { api } from "../api/client";
import { cn } from "../lib/utils";
import { Button, buttonVariants } from "./ui/button";
import { Kbd } from "./ui/kbd";

export function ExtensionLoadPath({
  compact = false,
  hideHint = false,
}: {
  compact?: boolean;
  hideHint?: boolean;
}) {
  const { data } = useQuery({
    queryKey: ["desktop-chrome"],
    queryFn: api.desktop.chrome,
  });
  const path = data?.extension_dir || "";
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!path) return;
    await navigator.clipboard.writeText(path);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const pathRow = path ? (
    <div className="mt-1.5 flex items-start gap-2">
      <code className="min-w-0 flex-1 rounded-md border border-border bg-muted/50 px-2 py-1.5 font-mono text-xs leading-relaxed break-all text-foreground">
        {path}
      </code>
      <Button type="button" variant="outline" size="xs" onClick={() => void copy()}>
        {copied ? <CheckIcon className="text-success" /> : <CopyIcon />}
        {copied ? "Copied" : "Copy path"}
      </Button>
    </div>
  ) : (
    <p className="mt-1 text-xs text-muted-foreground">Looking up the extension folder…</p>
  );

  return (
    <div className={cn("text-sm text-foreground/90", compact ? "mt-2" : "mt-3")}>
      {hideHint ? (
        pathRow
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <a
              className={buttonVariants({ variant: "outline", size: "xs" })}
              href={CHROME_WEB_STORE_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLinkIcon />
              Install from Chrome Web Store
            </a>
            <span className="text-xs text-muted-foreground">Recommended for most sellers.</span>
          </div>
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground">
              Or load from this Mac’s folder (Developer mode)
            </summary>
            <ol className="mt-2 flex list-decimal flex-col gap-1.5 ps-5 text-sm text-foreground/90 marker:text-muted-foreground">
              <li>
                Copy this folder path:
                {pathRow}
              </li>
              <li>
                In Chrome, open <code className="font-mono text-xs">chrome://extensions</code>.
              </li>
              <li>Turn on Developer mode (top right).</li>
              <li>Click Load unpacked.</li>
              <li>
                Press <Kbd>Control-Shift-G</Kbd> (<Kbd>⌘⇧G</Kbd>) to search for the folder.
              </li>
              <li>Paste the path, click Go, then Open.</li>
            </ol>
          </details>
        </div>
      )}
    </div>
  );
}
