import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon } from "lucide-react";
import { api } from "../api/client";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
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
        <ol className="flex list-decimal flex-col gap-1.5 ps-5 marker:text-muted-foreground">
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
      )}
    </div>
  );
}
