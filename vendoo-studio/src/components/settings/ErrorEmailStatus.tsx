import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { pollMs } from "../../api/polling";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function ErrorEmailStatus() {
  const { data, error } = useQuery({
    queryKey: ["error-email-delivery"],
    queryFn: api.settings.errorEmailDelivery,
    refetchInterval: () => (typeof document === "undefined" || document.hidden ? false : pollMs(30000)),
  });
  if (!data?.visible) return null;
  const labels = {
    disabled: "Disabled", needs_budget: "Waiting for a daily budget", ready: "Ready",
    quota_paused: "Paused by Google’s relay quota", delivery_failed: "Delivery failed",
    budget_exhausted: "Email budget used",
  };
  return (
    <SettingsSection id="error-email" title="Error emails">
      <SettingsRow id="error-email-delivery" title="Operator email delivery"
        description="Warnings are grouped into hourly digests. Errors use the reserved email budget."
        status={data.state ? labels[data.state] : undefined}>
        {error ? <SettingsNote tone="error">{error.message}</SettingsNote> : null}
        {data.state === "needs_budget" ? <SettingsNote>Reports are retained until the operator configures a verified daily allowance.</SettingsNote> : null}
        {data.state === "needs_budget" ? <SettingsNote>{data.pending} reports pending.</SettingsNote> : null}
        {data.daily_limit ? <SettingsNote>{data.used} of {data.daily_limit} email attempts in the last 24 hours; {data.pending} reports pending.</SettingsNote> : null}
        {data.last_error ? <SettingsNote tone="error">{data.last_error}</SettingsNote> : null}
        {data.resume_at ? <SettingsNote>Next eligible attempt: {new Date(data.resume_at * 1000).toLocaleString()}.</SettingsNote> : null}
        {data.last_success_at ? <SettingsNote>Last accepted by the relay: {new Date(data.last_success_at * 1000).toLocaleString()}.</SettingsNote> : null}
        {data.dropped ? <SettingsNote tone="error">{data.dropped} reports could not enter the email queue. Check the application logs.</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}
