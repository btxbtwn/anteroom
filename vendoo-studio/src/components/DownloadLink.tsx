import { useState, type ReactNode } from "react";
import { studioBlob } from "../api/transport";

export function DownloadLink({ href, filename, className, children }: {
  href: string; filename: string; className?: string; children: ReactNode;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  return <>
    <a href={href} download={filename} className={className} aria-busy={pending}
      onClick={async event => {
        event.preventDefault();
        if (pending) return;
        setPending(true);
        setError("");
        try {
          const blob = await studioBlob(href);
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = filename;
          document.body.append(link);
          link.click();
          link.remove();
          window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "Could not download this export.");
        } finally {
          setPending(false);
        }
      }}>{children}</a>
    {error && <span role="alert" className="text-sm text-destructive-foreground">{error}</span>}
  </>;
}
