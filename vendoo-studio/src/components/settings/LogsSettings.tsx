import { Fragment, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { pollMs } from "../../api/polling";
import { ChevronRightIcon, Trash2Icon } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

// Phones get one scroll for the page and each log row as a card: time, level and
// source on a line, the message (the tap target) under them.
const LOG_SCROLL =
  "max-h-[min(75vh,48rem)] overflow-auto rounded-lg border border-border/60 max-[900px]:max-h-none max-[900px]:overflow-visible";
const LOG_TABLE =
  "w-full min-w-[36rem] border-collapse text-left text-xs [&_td]:px-3 [&_td]:py-1.5 [&_td]:align-top [&_th]:px-3 [&_th]:py-2 "
  + "max-[900px]:block max-[900px]:min-w-0 max-[900px]:[&_thead]:hidden max-[900px]:[&_tbody]:block max-[900px]:[&_tr]:flex "
  + "max-[900px]:[&_tr]:flex-wrap max-[900px]:[&_tr]:items-baseline max-[900px]:[&_td]:py-1 max-[900px]:[&_td.w-full]:basis-full "
  + "max-[900px]:[&_td[colspan]]:basis-full max-[900px]:[&_tr]:py-1";
const LOG_HEAD =
  "sticky top-0 z-10 bg-background/95 text-3xs font-medium uppercase tracking-wider text-muted-foreground backdrop-blur-sm [&_th]:border-b [&_th]:border-border/60 [&_th]:font-medium";
const LOG_ROW = "border-b border-border/40 tabular-nums text-muted-foreground last:border-b-0";
const LOG_PRE =
  "max-h-80 overflow-auto rounded-md border border-border/60 bg-muted/40 p-2.5 font-mono text-2xs leading-relaxed whitespace-pre-wrap text-foreground [overflow-wrap:anywhere]";

function LogToggle({ open, onClick, children }: { open: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onClick}
      className="group flex w-full min-w-0 cursor-pointer items-start gap-1 rounded-sm text-left text-foreground outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring max-[900px]:min-h-11 max-[900px]:items-center max-[900px]:text-sm"
    >
      <ChevronRightIcon className={cn("mt-px size-3 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </button>
  );
}

function formatLogTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function ErrorLogSection() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["error-logs"],
    queryFn: api.settings.errorLogs,
    refetchInterval: () => (document.hidden ? false : pollMs(5000)),
  });
  const clear = useMutation({
    mutationFn: api.settings.clearErrorLogs,
    onSuccess: () => queryClient.setQueryData(["error-logs"], { entries: [] }),
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const entries = data?.entries ?? [];
  return (
    <SettingsSection id="errors" title="Errors">
      <SettingsRow
        id="error-logs"
        title="Warnings and errors"
        description="Problems since Anteroom opened, with keys masked; the list clears on restart but the log file keeps them."
        status={entries.length ? `${entries.length} ${entries.length === 1 ? "entry" : "entries"}` : undefined}
        control={
          <Button
            size="xs"
            variant="ghost-muted"
            disabled={!entries.length || clear.isPending}
            onClick={() => {
              setOpenId(null);
              clear.mutate();
            }}
          >
            <Trash2Icon />
            Clear
          </Button>
        }
      >
        {error ? <SettingsNote tone="error">{error.message}</SettingsNote> : null}
        {entries.length === 0 ? (
          <SettingsNote>No warnings or errors since Anteroom started.</SettingsNote>
        ) : (
          <div className={LOG_SCROLL}>
            <table className={LOG_TABLE} aria-label="Anteroom warnings and errors">
              <thead className={LOG_HEAD}>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Level</th>
                  <th scope="col">Source</th>
                  <th scope="col">Message</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const open = openId === entry.id;
                  return (
                    <Fragment key={entry.id}>
                      <tr className={cn(LOG_ROW, open && "bg-muted/30")}>
                        <td className="whitespace-nowrap">{formatLogTime(entry.at)}</td>
                        <td className={entry.level === "warning" ? "text-warning-foreground" : "text-destructive-foreground"}>
                          {entry.level}
                        </td>
                        <td className="whitespace-nowrap">{entry.source}</td>
                        <td className="w-full">
                          {entry.detail ? (
                            <LogToggle open={open} onClick={() => setOpenId(open ? null : entry.id)}>
                              {entry.message}
                            </LogToggle>
                          ) : (
                            <span className="pl-4 text-foreground [overflow-wrap:anywhere]">{entry.message}</span>
                          )}
                        </td>
                      </tr>
                      {open && entry.detail ? (
                        <tr className="border-b border-border/40 bg-muted/30 last:border-b-0">
                          <td colSpan={4} className="!pb-3">
                            <pre className={LOG_PRE}>{entry.detail}</pre>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SettingsRow>
    </SettingsSection>
  );
}

export function LogsSettings() {
  return <ErrorLogSection />;
}
