import { Fragment, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

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
    <section className="settings-api-log-detail-section">
      <h4>{title}</h4>
      {names.length ? (
        <dl className="settings-api-log-headers">
          {names.map((name) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{headers[name]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="settings-api-log-none">None</p>
      )}
    </section>
  );
}

function LogBody({ title, body }: { title: string; body: string | null }) {
  return (
    <section className="settings-api-log-detail-section">
      <h4>{title}</h4>
      {body ? <pre className="settings-api-log-body">{prettyLogBody(body)}</pre> : <p className="settings-api-log-none">None</p>}
    </section>
  );
}

function LogDetail({ id }: { id: number }) {
  const { data, error, isPending } = useQuery({
    queryKey: ["vendoo-api-log", id],
    queryFn: () => api.settings.vendooApiLog(id),
    staleTime: Infinity,
  });
  if (isPending) return <p className="settings-api-log-none">Loading…</p>;
  if (error) return <p className="settings-api-log-none text-error" role="alert">{error.message}</p>;
  return (
    <div className="settings-api-log-detail">
      <section className="settings-api-log-detail-section">
        <h4>General</h4>
        <dl className="settings-api-log-headers">
          <div><dt>URL</dt><dd>{data.url}</dd></div>
          <div><dt>Method</dt><dd>{data.method}</dd></div>
          <div><dt>Status</dt><dd>{data.status ?? "No response"}</dd></div>
          <div><dt>Time</dt><dd>{new Date(data.at).toLocaleString()}</dd></div>
          <div><dt>Duration</dt><dd>{data.duration_ms} ms</dd></div>
          {data.error ? <div><dt>Error</dt><dd>{data.error}</dd></div> : null}
        </dl>
      </section>
      <LogHeaders title="Request headers" headers={data.request_headers} />
      <LogBody title="Request body" body={data.request_body} />
      <LogHeaders title="Response headers" headers={data.response_headers} />
      <LogBody title="Response body" body={data.response_body} />
    </div>
  );
}

export function LogsSettings() {
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
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={!entries.length || clear.isPending}
            onClick={() => {
              setOpenId(null);
              clear.mutate();
            }}
          >
            Clear
          </button>
        }
      >
        {error ? <p className="settings-row-desc text-error" role="alert">{error.message}</p> : null}
        {entries.length === 0 ? (
          <p className="settings-row-desc settings-api-log-empty">
            No Vendoo calls yet. Create, update, or open a listing and requests will show up here.
          </p>
        ) : (
          <div className="settings-api-log">
            <table className="settings-api-log-table" aria-label="Vendoo API calls">
              <thead>
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
                      <tr className={[entry.ok ? "" : "is-failed", open ? "is-open" : ""].filter(Boolean).join(" ") || undefined}>
                        <td>{formatLogTime(entry.at)}</td>
                        <td>{entry.method}</td>
                        <td>{entry.status ?? entry.error ?? ""}</td>
                        <td>{entry.duration_ms} ms</td>
                        <td className="settings-api-log-path">
                          <button
                            type="button"
                            className="settings-api-log-toggle"
                            aria-expanded={open}
                            onClick={() => setOpenId(open ? null : entry.id)}
                          >
                            {entry.url}
                          </button>
                        </td>
                      </tr>
                      {open ? (
                        <tr className="settings-api-log-detail-row">
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

