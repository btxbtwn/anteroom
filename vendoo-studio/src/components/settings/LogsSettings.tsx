import { Fragment, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ChevronRightIcon, Trash2Icon } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const LOG_SCROLL = "max-h-[min(75vh,48rem)] overflow-auto rounded-lg border border-border/60";
const LOG_TABLE = "w-full min-w-[36rem] border-collapse text-left text-xs [&_td]:px-3 [&_td]:py-1.5 [&_td]:align-top [&_th]:px-3 [&_th]:py-2";
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
      className="group flex w-full min-w-0 cursor-pointer items-start gap-1 rounded-sm text-left text-foreground outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
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

function prettyLogBody(body: string): string {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

function LogHeaders({ title, headers }: { title: string; headers: Record<string, string> }) {
  const names = Object.keys(headers);
  return (
    <section className="space-y-1.5">
      <h4 className="text-3xs font-medium uppercase tracking-wider text-muted-foreground">{title}</h4>
      {names.length ? (
        <dl className="grid grid-cols-[minmax(8rem,max-content)_minmax(0,1fr)] gap-x-4 gap-y-1 font-mono text-2xs">
          {names.map((name) => (
            <div key={name} className="contents">
              <dt className="text-muted-foreground">{name}</dt>
              <dd className="text-foreground [overflow-wrap:anywhere]">{headers[name]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-xs text-muted-foreground">None</p>
      )}
    </section>
  );
}

function LogBody({ title, body }: { title: string; body: string | null }) {
  return (
    <section className="space-y-1.5">
      <h4 className="text-3xs font-medium uppercase tracking-wider text-muted-foreground">{title}</h4>
      {body ? <pre className={LOG_PRE}>{prettyLogBody(body)}</pre> : <p className="text-xs text-muted-foreground">None</p>}
    </section>
  );
}

function LogDetail({ id }: { id: number }) {
  const { data, error, isPending } = useQuery({
    queryKey: ["vendoo-api-log", id],
    queryFn: () => api.settings.vendooApiLog(id),
    staleTime: Infinity,
  });
  if (isPending) {
    return (
      <p className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground">
        <Spinner size="xs" tone="muted" />
        Loading
      </p>
    );
  }
  if (error) return <SettingsNote tone="error">{error.message}</SettingsNote>;
  return (
    <div className="grid gap-4 py-2">
      <LogHeaders
        title="General"
        headers={{
          URL: data.url,
          Method: data.method,
          Status: String(data.status ?? "No response"),
          Time: new Date(data.at).toLocaleString(),
          Duration: `${data.duration_ms} ms`,
          ...(data.error ? { Error: data.error } : {}),
        }}
      />
      <LogHeaders title="Request headers" headers={data.request_headers} />
      <LogBody title="Request body" body={data.request_body} />
      <LogHeaders title="Response headers" headers={data.response_headers} />
      <LogBody title="Response body" body={data.response_body} />
    </div>
  );
}

function ErrorLogSection() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["error-logs"],
    queryFn: api.settings.errorLogs,
    refetchInterval: () => (document.hidden ? false : 5000),
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
        description="Problems Studio ran into while it was open, such as an AI provider rejecting its key or photos it could not read. Click one with details to see them. Keys and tokens are masked. The list clears when Studio restarts; the log file keeps them."
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
          <SettingsNote>No warnings or errors since Studio started.</SettingsNote>
        ) : (
          <div className={LOG_SCROLL}>
            <table className={LOG_TABLE} aria-label="Studio warnings and errors">
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

function VendooApiLogSection() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["vendoo-api-logs"],
    queryFn: api.settings.vendooApiLogs,
    refetchInterval: () => (document.hidden ? false : 1000),
  });
  const clear = useMutation({
    mutationFn: api.settings.clearVendooApiLogs,
    onSuccess: () => queryClient.setQueryData(["vendoo-api-logs"], { entries: [] }),
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const entries = data?.entries ?? [];
  return (
    <SettingsSection id="logs" title="Vendoo API">
      <SettingsRow
        id="vendoo-api-logs"
        title="Live requests"
        description="Calls the extension makes to Vendoo while Studio is open. Click a request to see its full URL, headers, and bodies. Tokens, API keys, and cookies are masked. The list clears when Studio restarts."
        status={entries.length ? `${entries.length} ${entries.length === 1 ? "call" : "calls"}` : undefined}
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
          <SettingsNote>No Vendoo calls yet. Create, update, or open a listing and requests will show up here.</SettingsNote>
        ) : (
          <div className={LOG_SCROLL}>
            <table className={LOG_TABLE} aria-label="Vendoo API calls">
              <thead className={LOG_HEAD}>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Method</th>
                  <th scope="col">Status</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Request</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const open = openId === entry.id;
                  return (
                    <Fragment key={entry.id}>
                      <tr className={cn(LOG_ROW, open && "bg-muted/30")}>
                        <td className="whitespace-nowrap">{formatLogTime(entry.at)}</td>
                        <td className="font-mono">{entry.method}</td>
                        <td className={entry.ok ? "text-success-foreground" : "text-destructive-foreground"}>
                          {entry.status ?? entry.error ?? ""}
                        </td>
                        <td className="whitespace-nowrap">{entry.duration_ms} ms</td>
                        <td className="w-full font-mono">
                          <LogToggle open={open} onClick={() => setOpenId(open ? null : entry.id)}>
                            {entry.url}
                          </LogToggle>
                        </td>
                      </tr>
                      {open ? (
                        <tr className="border-b border-border/40 bg-muted/30 last:border-b-0">
                          <td colSpan={5}>
                            <LogDetail id={entry.id} />
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
  return (
    <>
      <ErrorLogSection />
      <VendooApiLogSection />
    </>
  );
}
